"""Stage-2 of the round-2 pipeline.

Walks every reviewed item that needs regenerating and prepares its
act-img-gen state so a subsequent `generate.py --items …` run regenerates
the image with an updated prompt.

Three buckets are handled:

  1. specific feedback (>"regen.")    → rewrite prompt via gpt-5-mini using
                                         the German reviewer feedback as
                                         guidance, keep the trailing style
                                         block intact.
  2. just_regen                        → keep the prompt, just re-roll. Image
                                         model is non-deterministic so a
                                         fresh attempt often resolves the
                                         issue.
  3. both_bad without feedback         → same as #2 (re-roll).

For every item: clears `image_results` in the act-img-gen manifest so
generate.py treats the item as needing a fresh image. The previous prompt
is archived under `manifest.llm_history` and the previous image stays in
git history.

Run:
    python tools/prepare_round2.py             # do it
    python tools/prepare_round2.py --dry-run   # plan + cost estimate only
    python tools/prepare_round2.py --limit 5   # only process first 5 (test)

Required env: OPENAI_API_KEY (read from act-img-gen/.env).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
ACT_ROOT = ROOT.parent / "act-img-gen"
PROMPTS_ROOT = ACT_ROOT / "output" / "comic_v4"
REVIEW_FILE = ROOT / "review.json"

OPENAI_BASE = "https://api.openai.com/v1"
LLM_MODEL = "gpt-5-mini"
LLM_PRICING = {"prompt": 0.25e-6, "completion": 2.00e-6}


REWRITE_SYSTEM = """\
Du bist ein Prompt-Rewriter für ein KI-Bildgenerierungssystem (gpt-image-2,
quality=low, 1024×1024). Pro Aufruf bekommst du:
  1. den ursprünglichen englischen Bild-Prompt
  2. das deutsche Feedback eines menschlichen Reviewers, der das vom
     ursprünglichen Prompt erzeugte Bild als fehlerhaft markiert hat
  3. den deutschen + englischen Item-Namen

Aufgabe: gib einen überarbeiteten englischen Bild-Prompt zurück, der das
Reviewer-Feedback so direkt wie möglich umsetzt.

Regeln:
  - Behalte den Style-Block am Ende des Prompts WÖRTLICH bei. Das ist der
    Block, der mit "Style: schematic flat vector illustration ..." beginnt
    und mit "... Square 1024×1024." endet. Ändere ihn nicht, kürze ihn nicht.
  - Ändere nur den Item-beschreibenden Teil davor.
  - Halte dich an dieselben Sprach- und Stil-Konventionen wie der
    Original-Prompt: kurze Sätze, visuelle Beschreibung, keine
    Marken/Logos/Text/Hände/Besteck im Bild.
  - Bei Moderations-sensiblen Items (rohe Innereien/Fleisch/Fisch): vermeide
    "muscular", "fleshy", "moist", "glossy moist", "pinkish-beige flesh" —
    nutze kulinarische Standardbegriffe ("tripe", "liver", "fillet") und
    neutrale Farben ("off-white", "pale cream").
  - Wenn das Feedback einen unverarbeiteten Bestandteil betrifft ("Quark
    eingebacken", "Buttermilch nicht zu sehen"), entferne die explizite
    Erwähnung dieses Bestandteils im Bild-Prompt komplett — er soll
    visuell nicht auftauchen.
  - Wenn das Feedback eine Form-/Behälter-Vorgabe macht ("als Glas
    darstellen", "als offene Dose"), übernimm exakt diese Form.

Gib genau EINEN englischen Bild-Prompt zurück. Keine Markdown-
Auszeichnungen, keine Anführungszeichen, keine Vor- oder Nachrede.
"""


def load_dotenv(p: Path) -> None:
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        os.environ.setdefault(k, v)


def is_just_regen(fb: str) -> bool:
    bare = "".join(c for c in fb.strip().lower() if c.isalpha())
    return bare in {"regen", "regenerate", "rgen", "regne", "regenrate"}


def classify(reviews: dict) -> tuple[list[str], list[str], list[str]]:
    """Returns (specific, just_regen, both_bad_no_fb) code lists.

    Entries with a `resolution` field (e.g. "dedup", "revert") are skipped —
    those items have an explicit fix and must NOT be regenerated."""
    specific, just_regen, both_bad_no_fb = [], [], []
    for code, v in reviews.items():
        if v.get("resolution"):
            continue
        fb = v.get("feedback", "").strip()
        bb = v.get("bg_choice") == "both_bad"
        if fb and not is_just_regen(fb):
            specific.append(code)
        elif fb and is_just_regen(fb):
            just_regen.append(code)
        elif bb:
            both_bad_no_fb.append(code)
    return sorted(specific), sorted(just_regen), sorted(both_bad_no_fb)


def call_rewriter(api_key: str, name_de: str, name_en: str,
                  original_prompt: str, feedback: str) -> dict:
    user_block = (
        f"BLS-Item:\n"
        f"  Name (de): {name_de}\n"
        f"  Name (en): {name_en}\n\n"
        f"Original-Prompt:\n{original_prompt.strip()}\n\n"
        f"Reviewer-Feedback (deutsch):\n{feedback.strip()}\n\n"
        "Erzeuge jetzt den überarbeiteten englischen Bild-Prompt."
    )
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": user_block},
        ],
        "reasoning_effort": "minimal",
    }
    r = requests.post(f"{OPENAI_BASE}/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    cost = (usage.get("prompt_tokens", 0) * LLM_PRICING["prompt"]
            + usage.get("completion_tokens", 0) * LLM_PRICING["completion"])
    return {"text": text, "usage": usage, "cost_usd": cost,
            "system_prompt": REWRITE_SYSTEM, "user_message": user_block}


def archive_and_clear_image(manifest: dict) -> None:
    """Move existing image_results into image_history and clear, so
    generate.py treats this item as needing a fresh image. We keep the
    on-disk PNG (git-LFS history is the recovery path) but the manifest
    forgets it."""
    old = manifest.get("image_results", [])
    if old:
        manifest.setdefault("image_history", []).extend(old)
    manifest["image_results"] = []


def update_prompt(manifest: dict, item_dir: Path,
                  new_prompt: str, rewriter: dict, feedback: str) -> None:
    """Move old llm into llm_history, install new prompt + audit trail."""
    old_llm = manifest.get("llm")
    if old_llm:
        hist = manifest.setdefault("llm_history", [])
        hist.append({**old_llm, "superseded_at": utc_now(),
                     "superseded_by_feedback": feedback})
    manifest["llm"] = {
        "text": new_prompt,
        "input_tokens": rewriter["usage"].get("prompt_tokens", 0),
        "output_tokens": rewriter["usage"].get("completion_tokens", 0),
        "cost_usd": rewriter["cost_usd"],
        "model": LLM_MODEL,
        "provider": "openai",
        "system_prompt": rewriter["system_prompt"],
        "user_message": rewriter["user_message"],
        "reasoning_effort": "minimal",
        "rewrite_round": 2,
        "reviewer_feedback": feedback,
    }
    (item_dir / "prompt.md").write_text(new_prompt + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="process only the first N specific-feedback items "
                         "(useful for a smoke-test run)")
    args = ap.parse_args()

    load_dotenv(ACT_ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not args.dry_run:
        sys.exit("OPENAI_API_KEY not set (act-img-gen/.env)")

    if not REVIEW_FILE.exists():
        sys.exit(f"missing {REVIEW_FILE}")
    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    reviews = state.get("reviews", {})

    specific, just_regen, both_bad = classify(reviews)
    if args.limit:
        specific = specific[:args.limit]
    total = len(specific) + len(just_regen) + len(both_bad)
    print(f"Buckets:")
    print(f"  specific feedback (LLM rewrite): {len(specific)}")
    print(f"  just-regen (prompt unchanged):   {len(just_regen)}")
    print(f"  both_bad no-fb (prompt unch.):   {len(both_bad)}")
    print(f"  total to mark for regen:         {total}")

    if args.dry_run:
        # rough estimate: gpt-5-mini ~$0.001 per call
        print(f"\nEst. rewrite cost: ${len(specific) * 0.001:.2f}")
        print("(no changes written)")
        return 0

    rewrite_cost = 0.0
    n_rewrote = 0
    n_cleared = 0
    n_missing = 0

    # 1. Specific-feedback items: rewrite prompt + clear image
    for i, code in enumerate(specific, 1):
        item_dir = PROMPTS_ROOT / code
        manifest_path = item_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"  [{i}/{len(specific)}] {code}: no manifest, skip")
            n_missing += 1
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        v = reviews[code]
        feedback = v.get("feedback", "").strip()
        original = (manifest.get("llm") or {}).get("text", "")
        if not original:
            print(f"  [{i}/{len(specific)}] {code}: no original prompt, skip")
            n_missing += 1
            continue

        # Idempotency: skip the LLM call if the manifest already reflects
        # a rewrite for this exact feedback. This handles re-runs of
        # prepare_round2 in the same round (e.g. after a cancelled batch)
        # without burning tokens, but still re-rewrites when the user has
        # given fresh feedback for the next round.
        prev_fb = (manifest.get("llm") or {}).get("reviewer_feedback", "")
        if prev_fb and prev_fb.strip() == feedback:
            archive_and_clear_image(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
            n_rewrote += 1  # already up-to-date for this feedback
            continue

        try:
            rew = call_rewriter(api_key, manifest["name_de"],
                                manifest.get("name_en", ""),
                                original, feedback)
        except requests.HTTPError as e:
            print(f"  [{i}/{len(specific)}] {code}: rewriter HTTP {e.response.status_code}, retry once")
            time.sleep(2)
            try:
                rew = call_rewriter(api_key, manifest["name_de"],
                                    manifest.get("name_en", ""),
                                    original, feedback)
            except Exception as e2:
                print(f"  [{i}/{len(specific)}] {code}: rewriter failed: {e2}")
                n_missing += 1
                continue
        except Exception as e:
            print(f"  [{i}/{len(specific)}] {code}: rewriter failed: {e}")
            n_missing += 1
            continue

        update_prompt(manifest, item_dir, rew["text"], rew, feedback)
        archive_and_clear_image(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        rewrite_cost += rew["cost_usd"]
        n_rewrote += 1
        if i <= 5 or i % 25 == 0:
            print(f"  [{i}/{len(specific)}] {code}: ${rew['cost_usd']:.4f}  "
                  f"fb='{feedback[:60]}'")

    # 2 + 3. Just-regen / both-bad: only clear image_results
    for code in just_regen + both_bad:
        item_dir = PROMPTS_ROOT / code
        manifest_path = item_dir / "manifest.json"
        if not manifest_path.exists():
            n_missing += 1
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest.get("image_results"):
            continue  # already cleared
        archive_and_clear_image(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        n_cleared += 1

    print()
    print(f"Done.")
    print(f"  Prompts rewritten:        {n_rewrote}")
    print(f"  Image-results cleared:    {n_cleared} (just-regen / both-bad)")
    print(f"  Missing/skipped:          {n_missing}")
    print(f"  Rewrite cost:             ${rewrite_cost:.2f}")
    print()
    print("Next:")
    print(f"  cd {ACT_ROOT}")
    print(f"  python3 generate.py --items-csv data/items_pipeline.csv \\")
    print(f"      --items {' '.join((specific + just_regen + both_bad)[:3])} ... -y")
    print("  (full code list in tools/prepare_round2.py output)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
