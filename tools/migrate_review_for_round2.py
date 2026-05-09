"""Stage-5 prep: migrate review.json for a fresh round-2 review pass.

For every item that's about to be regenerated (specific feedback +
just-regen + both-bad), move its existing review entry into
`review_history[code]` (an append-only list of past entries) and remove
it from the active `reviews` dict — so review.py sees these items as
unreviewed again. Then set `current_index` to the first regen item.

review.py reads `review_history` and shows a "Round N — previously you
said: ..." hint above the current item when it exists, so during round 2
you immediately see what was supposed to be fixed.

Run after Stage 4 (when round-2 images are on disk):
    python tools/migrate_review_for_round2.py             # do it
    python tools/migrate_review_for_round2.py --dry-run   # preview
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_FILE = ROOT / "review.json"


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

    if not REVIEW_FILE.exists():
        sys.exit(f"missing {REVIEW_FILE}")

    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    reviews = state.setdefault("reviews", {})
    history = state.setdefault("review_history", {})

    codes = codes_to_regen(reviews)
    print(f"{len(codes)} items to migrate")

    if args.dry_run:
        for c in codes[:5]:
            v = reviews[c]
            print(f"  {c}  bg_choice={v.get('bg_choice')}  fb='{v.get('feedback','')[:60]}'")
        if len(codes) > 5:
            print(f"  ... +{len(codes) - 5}")
        return 0

    for code in codes:
        prev = reviews.pop(code, None)
        if prev is None:
            continue
        # Round number = position in this code's history list (1-indexed).
        # Round 1 entries are the original review; round 2 = first regen
        # review; round 3+ = later regens. So this is just the count of
        # entries that already exist (since we are about to add another).
        round_n = len(history.get(code, [])) + 1
        history.setdefault(code, []).append({
            **prev,
            "round": round_n,
        })

    # find first regen code in the canonical order so the user starts there
    import csv
    first = None
    with (ROOT / "items.csv").open(encoding="utf-8") as f:
        all_codes = [r["code"] for r in csv.DictReader(f)]
    regen_set = set(codes)
    for i, c in enumerate(all_codes):
        if c in regen_set:
            first = i
            break
    if first is not None:
        state["current_index"] = first
        print(f"current_index → {first}  ({all_codes[first]})")

    REVIEW_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done. review.json updated; {len(codes)} items moved to review_history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
