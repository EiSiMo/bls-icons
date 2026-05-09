#!/usr/bin/env python3
"""End-to-end orchestrator for the BLS icons pipeline.

Steps run sequentially, each idempotent:

  1. Adapt items.csv (code,name_de,name_en) → act-img-gen format (sbls,…,hauptgruppe)
  2. Submit OpenAI batch (gpt-5-mini prompts + gpt-image-2 batch images)
  3. Poll until batch completes, fetch images (no local bg-removal)
  4. Detect moderation-blocked items, regenerate prompt + image sync
  5. Sync raw PNGs into bls-icons/icons_raw/
  6. Modal GPU bg-removal: icons_raw/ → icons/
  7. Build the HTML viewer

Designed to run unattended: no interactive prompts, retries with backoff on
transient errors, logs to pipeline.log. Crash-safe — re-running picks up
where it left off because every sub-step is idempotent.

Usage:
    python3 run_pipeline.py                         # full run on items.csv
    python3 run_pipeline.py --items-csv subset.csv  # custom subset
    python3 run_pipeline.py --dry-run               # cost estimate only
    python3 run_pipeline.py --resume                # skip submit, only fetch+downstream
    python3 run_pipeline.py --skip-modal            # everything except Modal step
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACT = ROOT.parent / "act-img-gen"
LOG = ROOT / "pipeline.log"
ITEMS_CSV_DEFAULT = ROOT / "items.csv"

OPENAI_BASE = "https://api.openai.com/v1"
IMAGE_MODEL = "gpt-image-2"
QUALITY = "low"
SLUG = f"openai__{IMAGE_MODEL}-{QUALITY}"


# ---------------------------------------------------------------------------
# Logging — one line per major event, timestamps in UTC
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Subprocess helper — UTF-8 enforced, log streamed
# ---------------------------------------------------------------------------

def run_cmd(cmd: list[str], cwd: Path | None = None, retries: int = 0,
            tag: str = "") -> int:
    """Run a subprocess. Tee stdout+stderr to console AND pipeline.log so
    everything is recoverable post-mortem. tag is prefixed to each captured
    line so log readers can tell which sub-step produced what."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    prefix = f"[{tag}] " if tag else ""
    for attempt in range(retries + 1):
        log(f"$ {' '.join(cmd)}" + (f"  (attempt {attempt + 1})" if attempt else ""))
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, encoding="utf-8", errors="replace",
            )
        except Exception as e:
            log(f"  subprocess spawn error: {e}")
            if attempt < retries:
                wait = 30 * (attempt + 1)
                log(f"  retrying in {wait}s …")
                time.sleep(wait)
            continue

        with LOG.open("a", encoding="utf-8") as logf:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                # Console: as-is. Log: with tag prefix.
                print(line, flush=True)
                logf.write(f"{prefix}{line}\n")
                logf.flush()
        proc.wait()
        if proc.returncode == 0:
            return 0
        log(f"  exit code {proc.returncode}")
        if attempt < retries:
            wait = 30 * (attempt + 1)
            log(f"  retrying in {wait}s …")
            time.sleep(wait)
    return 1


# ---------------------------------------------------------------------------
# CSV adapter — bls-icons format → act-img-gen format
# ---------------------------------------------------------------------------

def adapt_csv(src_csv: Path) -> Path:
    """Convert items.csv (code,name_de,name_en) into the format generate.py expects
    (sbls,name_de,name_en,hauptgruppe). Output goes into act-img-gen/data/."""
    dst = ACT / "data" / f"{src_csv.stem}_pipeline.csv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src_csv.open(encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        cols = set(reader.fieldnames or [])
        code_col = "sbls" if "sbls" in cols else "code"
        if code_col not in cols:
            raise SystemExit(f"{src_csv}: need column 'code' or 'sbls'")
        rows = list(reader)
    with dst.open("w", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        w.writerow(["sbls", "name_de", "name_en", "hauptgruppe"])
        for r in rows:
            code = r[code_col]
            w.writerow([code, r.get("name_de", ""), r.get("name_en", ""), code[0]])
    log(f"adapted CSV: {src_csv.name} → {dst.relative_to(ROOT.parent)} ({len(rows)} items)")
    return dst


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_submit(pipeline_csv: Path, dry_run: bool) -> int:
    cmd = [
        sys.executable, "-X", "utf8", str(ACT / "generate.py"),
        "--items-csv", str(pipeline_csv),
    ]
    cmd.append("--dry-run" if dry_run else "-y")
    return run_cmd(cmd, retries=2, tag="submit")


def step_fetch() -> int:
    cmd = [
        sys.executable, "-X", "utf8", str(ACT / "generate.py"),
        "--fetch", "latest",
        "--no-postprocess",
        "--poll-interval", "60",
    ]
    return run_cmd(cmd, retries=3, tag="fetch")


def step_regen_blocked(pipeline_csv: Path, max_retries: int = 2) -> int:
    """Find items where the batch returned moderation_blocked, regenerate the
    prompt (which now uses Rule F-aware PROMPTER_SYSTEM) and try sync. Return
    count of items still blocked after retries."""
    out_root = ACT / "output" / "comic_v4"
    blocked = _scan_blocked(out_root)
    if not blocked:
        log("step_regen: no moderation-blocked items")
        return 0

    log(f"step_regen: {len(blocked)} blocked items: {blocked}")
    name_lookup: dict[str, tuple[str, str]] = {}
    with pipeline_csv.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name_lookup[r["sbls"]] = (r["name_de"], r["name_en"])

    still_blocked: list[str] = []
    for sbls in blocked:
        item_dir = out_root / sbls
        de, en = name_lookup.get(sbls, ("", ""))
        ok = False
        for attempt in range(max_retries):
            ok = _regen_one(sbls, de, en, item_dir, attempt)
            if ok:
                break
        if not ok:
            still_blocked.append(sbls)

    if still_blocked:
        log(f"step_regen: STILL_BLOCKED after retries: {still_blocked}")
    return len(still_blocked)


def _scan_blocked(out_root: Path) -> list[str]:
    blocked = []
    for item_dir in sorted(out_root.iterdir()):
        if not item_dir.is_dir() or item_dir.name.startswith("_"):
            continue
        mf = item_dir / "manifest.json"
        if not mf.exists():
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        canonical = item_dir / f"{SLUG}.png"
        # Already has an image? skip — was successfully (re)generated
        if canonical.exists():
            continue
        for r in m.get("image_results", []):
            err = r.get("error", "")
            if "moderation_blocked" in err or "safety_violations" in err:
                blocked.append(item_dir.name)
                break
    return blocked


def _regen_one(sbls: str, name_de: str, name_en: str, item_dir: Path,
               attempt: int) -> bool:
    """Regenerate prompt (with current PROMPTER_SYSTEM, which has Rule F) and
    re-call gpt-image-2 sync. Returns True on success."""
    import importlib
    sys.path.insert(0, str(ACT))
    g = importlib.import_module("generate")
    g.load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log(f"  {sbls}: no OPENAI_API_KEY")
        return False
    style_spec = (ACT / "style" / "comic_v4.md").read_text(encoding="utf-8").strip()
    item = {"sbls": sbls, "name_de": name_de, "name_en": name_en}

    # Force fresh prompt each attempt (don't reuse prior cached prompt)
    try:
        llm = g.call_llm(api_key, style_spec, item)
    except Exception as e:
        log(f"  {sbls}: prompt regen failed: {e}")
        return False
    prompt = llm["text"]
    log(f"  {sbls} attempt {attempt + 1}: new prompt ({len(prompt)} chars)")
    (item_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")

    mf = item_dir / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["llm"] = llm
    # Drop prior moderation errors so we don't double-record
    m["image_results"] = [r for r in m.get("image_results", [])
                          if not ("moderation_blocked" in r.get("error", "")
                                  or "safety_violations" in r.get("error", ""))]
    mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    import requests
    try:
        r = requests.post(
            f"{OPENAI_BASE}/images/generations",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": IMAGE_MODEL, "prompt": prompt, "size": "1024x1024",
                  "quality": QUALITY, "n": 1},
            timeout=300,
        )
    except Exception as e:
        log(f"  {sbls}: HTTP error: {e}")
        return False

    if not r.ok:
        body = r.text[:300]
        log(f"  {sbls}: HTTP {r.status_code}: {body}")
        if "moderation_blocked" in body or "safety_violations" in body:
            return False  # caller may retry with another prompt regen
        return False

    data = r.json()
    if not data.get("data"):
        log(f"  {sbls}: empty response")
        return False
    raw = base64.b64decode(data["data"][0]["b64_json"])
    (item_dir / f"{SLUG}.png").write_bytes(raw)

    usage = data.get("usage", {})
    m["image_results"].append({
        "model": f"openai/{IMAGE_MODEL}@{QUALITY}",
        "file": f"{SLUG}.png",
        "usage": usage,
        "mode": "sync_regen_after_moderation",
        "regen_attempt": attempt + 1,
    })
    mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  {sbls}: regenerated OK")
    return True


def step_sync_raw() -> int:
    return run_cmd([sys.executable, "-X", "utf8", str(ROOT / "sync_icons.py")],
                   tag="sync")


def step_modal() -> int:
    cmd = [
        sys.executable, "-X", "utf8", "-m", "modal", "run",
        str(ROOT / "modal_postprocess.py"),
        "--in-dir", str(ROOT / "icons_raw"),
        "--out-dir", str(ROOT / "icons"),
    ]
    return run_cmd(cmd, retries=2, tag="modal")


def step_viewer() -> int:
    return run_cmd([sys.executable, "-X", "utf8", str(ROOT / "build_viewer.py")],
                   tag="viewer")


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

def summarize(pipeline_csv: Path) -> dict:
    """Tally what's done vs missing."""
    expected = set()
    with pipeline_csv.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            expected.add(r["sbls"])
    in_icons = {p.stem for p in (ROOT / "icons").glob("*.png")} if (ROOT / "icons").exists() else set()
    in_raw = {p.stem for p in (ROOT / "icons_raw").glob("*.png")} if (ROOT / "icons_raw").exists() else set()
    in_modal = {p.stem for p in (ROOT / "icons_modal").glob("*.png")} if (ROOT / "icons_modal").exists() else set()
    return {
        "expected": len(expected),
        "raw": len(expected & in_raw),
        "alpha": len(expected & in_icons),
        "modal": len(expected & in_modal),
        "missing_raw": sorted(expected - in_raw)[:20],
        "missing_alpha": sorted(expected - in_icons)[:20],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--items-csv", default=str(ITEMS_CSV_DEFAULT),
                   help="Source CSV (columns code,name_de,name_en or sbls,…)")
    p.add_argument("--dry-run", action="store_true",
                   help="Cost estimate only — no API calls")
    p.add_argument("--resume", action="store_true",
                   help="Skip submit; assume a batch already exists, just fetch")
    p.add_argument("--skip-modal", action="store_true",
                   help="Skip Modal step (e.g. for testing)")
    p.add_argument("--skip-regen", action="store_true",
                   help="Skip moderation-regen step")
    args = p.parse_args()

    src_csv = Path(args.items_csv)
    if not src_csv.exists():
        sys.exit(f"items csv not found: {src_csv}")

    log("=" * 60)
    log(f"START — items={src_csv} dry_run={args.dry_run} resume={args.resume}")

    # Pre-flight: fail fast if creds missing, before we burn money
    if not args.dry_run:
        env_file = ACT / ".env"
        env_has_key = env_file.exists() and "OPENAI_API_KEY" in env_file.read_text(encoding="utf-8")
        if not (env_has_key or os.environ.get("OPENAI_API_KEY")):
            log("PREFLIGHT_FAIL: no OPENAI_API_KEY in act-img-gen/.env or environment")
            return 1
        log("preflight: OPENAI_API_KEY present")

        if not args.skip_modal:
            modal_toml = Path.home() / ".modal.toml"
            if not modal_toml.exists():
                log("PREFLIGHT_FAIL: no ~/.modal.toml — run `python3 -m modal token new`")
                return 1
            log("preflight: modal token present")

    pipeline_csv = adapt_csv(src_csv)

    # Step 1+2: submit (unless --resume) + fetch
    if not args.resume:
        log("STEP 1: submit batch")
        if step_submit(pipeline_csv, args.dry_run) != 0:
            log("STEP 1: FAILED")
            return 1
        if args.dry_run:
            log("dry-run complete")
            return 0
    else:
        log("STEP 1: skipped (--resume)")

    log("STEP 2: fetch batch")
    if step_fetch() != 0:
        log("STEP 2: completed with errors — continuing anyway")

    # Step 3: regen moderation-blocked items
    if not args.skip_regen:
        log("STEP 3: regen moderation-blocked items")
        still_blocked = step_regen_blocked(pipeline_csv)
        if still_blocked:
            log(f"STEP 3: {still_blocked} items still blocked after retries — review manually")
    else:
        log("STEP 3: skipped (--skip-regen)")

    # Step 4: sync raw PNGs
    log("STEP 4: sync raw PNGs to bls-icons/")
    if step_sync_raw() != 0:
        log("STEP 4: FAILED")
        return 1

    # Step 5: Modal bg-removal
    if not args.skip_modal:
        log("STEP 5: Modal GPU bg-removal")
        if step_modal() != 0:
            log("STEP 5: FAILED — viewer will still build but icons/ may be incomplete")
    else:
        log("STEP 5: skipped (--skip-modal)")

    # Step 6: build viewer
    log("STEP 6: build viewer")
    step_viewer()

    # Final summary
    s = summarize(pipeline_csv)
    log("=" * 60)
    pass_fail = "PASS" if s["alpha"] == s["expected"] else "PARTIAL"
    log(f"{pass_fail} — expected={s['expected']} raw={s['raw']} alpha={s['alpha']} modal={s['modal']}")
    if s["missing_raw"]:
        log(f"  MISSING_RAW (first 20): {s['missing_raw']}")
    if s["missing_alpha"]:
        log(f"  MISSING_ALPHA (first 20): {s['missing_alpha']}")
    log(f"viewer: {ROOT / 'index.html'}")
    log(f"log:    {LOG}")
    log("debug:  grep -E 'ERROR|FAIL|exit code|MISSING|STILL_BLOCKED' pipeline.log")
    return 0 if pass_fail == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
