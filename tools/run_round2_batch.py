"""Stage-3: submit the round-2 image-regen batch.

Reads review.json, computes the 574 codes that need regenerating
(specific feedback + just-regen + both-bad), then spawns
`act-img-gen/generate.py --items <codes> -y`. generate.py picks up the
prompts already written by tools/prepare_round2.py (cached in each
manifest's `llm.text`) and submits a single OpenAI Batch job for the lot.

After submission you fetch with:
    cd ../act-img-gen
    python generate.py --fetch latest

Run:
    python tools/run_round2_batch.py             # submit
    python tools/run_round2_batch.py --dry-run   # show codes + cost only
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACT_ROOT = ROOT.parent / "act-img-gen"
REVIEW_FILE = ROOT / "review.json"
ICONS_RAW = ROOT / "icons_raw"
ICONS_ALPHA = ROOT / "icons"
PREV_RAW = ROOT / "icons_round1_raw"
PREV_ALPHA = ROOT / "icons_round1_alpha"


def is_just_regen(fb: str) -> bool:
    bare = "".join(c for c in fb.strip().lower() if c.isalpha())
    return bare in {"regen", "regenerate", "rgen", "regne", "regenrate"}


def codes_to_regen(reviews: dict) -> list[str]:
    out = []
    for code, v in reviews.items():
        if v.get("resolution"):  # explicitly handled (dedup/revert), skip
            continue
        fb = v.get("feedback", "").strip()
        if fb or v.get("bg_choice") == "both_bad":
            out.append(code)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    codes = codes_to_regen(state.get("reviews", {}))
    print(f"{len(codes)} codes to regenerate")

    # Sanity-check: verify each code has a manifest with a non-empty prompt
    # cached in act-img-gen — otherwise generate.py would call the original
    # prompter again and undo our rewrites for items where prepare_round2
    # already updated llm.text.
    missing_prompt = []
    cleared_image = 0
    for c in codes:
        mp = ACT_ROOT / "output" / "comic_v4" / c / "manifest.json"
        if not mp.exists():
            missing_prompt.append(c)
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        if not (m.get("llm") or {}).get("text"):
            missing_prompt.append(c)
        if not m.get("image_results"):
            cleared_image += 1
    print(f"  with cached prompt: {len(codes) - len(missing_prompt)}")
    print(f"  with cleared image_results (ready for regen): {cleared_image}")
    if missing_prompt:
        print(f"  WARNING: {len(missing_prompt)} items have no cached prompt; "
              f"generate.py would re-call the original prompter for these.")
        for c in missing_prompt[:5]:
            print(f"    {c}")

    # Cost estimate: gpt-image-2 quality=low at batch tariff ~$0.0042/item
    est = len(codes) * 0.0042
    print(f"\nEst. image batch cost: ${est:.2f}  ({len(codes)} × $0.0042)")

    if args.dry_run:
        return 0

    # Snapshot the current images for codes about to be regenerated, so the
    # next-round review tool can show small "previous version" thumbnails
    # alongside the new ones. Uses the icons_round1_*/ directories — name is a
    # historical artefact, contents are always "the most recent prior image".
    PREV_RAW.mkdir(exist_ok=True)
    PREV_ALPHA.mkdir(exist_ok=True)
    n_snap = 0
    for c in codes:
        for src_dir, dst_dir in [(ICONS_RAW, PREV_RAW), (ICONS_ALPHA, PREV_ALPHA)]:
            src = src_dir / f"{c}.png"
            if src.exists():
                shutil.copy2(src, dst_dir / f"{c}.png")
        n_snap += 1
    print(f"\nSnapshotted {n_snap} prior images → icons_round1_*/")

    cmd = [sys.executable, "generate.py",
           "--items-csv", "data/items_pipeline.csv",
           "-y", "--items", *codes]
    print(f"\nSpawning generate.py in {ACT_ROOT}")
    print(f"  ({len(cmd)} argv items, ~{sum(len(a) for a in cmd)} chars)")
    result = subprocess.run(cmd, cwd=str(ACT_ROOT))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
