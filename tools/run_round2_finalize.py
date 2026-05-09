"""Stage-4: after generate.py --fetch downloads the round-2 batch, copy
the new white-bg PNGs into icons_raw/, drop the now-stale round-1 alphas
in icons/, and run Modal background removal on the round-2 set.

Run:
    python tools/run_round2_finalize.py             # do all three steps
    python tools/run_round2_finalize.py --dry-run   # show plan only
    python tools/run_round2_finalize.py --no-modal  # skip the modal step
                                                    #   (run modal yourself)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "icons"
REVIEW_FILE = ROOT / "review.json"
SYNC_SCRIPT = ROOT / "tools" / "sync_icons.py"
MODAL_SCRIPT = ROOT / "modal_postprocess.py"


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
    ap.add_argument("--no-modal", action="store_true",
                    help="skip the Modal step (run `modal run "
                         "modal_postprocess.py` yourself afterwards)")
    args = ap.parse_args()

    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    codes = codes_to_regen(state.get("reviews", {}))
    print(f"{len(codes)} round-2 codes to finalize")

    if args.dry_run:
        return 0

    # 1. sync new images from act-img-gen → icons_raw/
    print(f"\n=== 1/3: sync_icons.py ===")
    r = subprocess.run([sys.executable, str(SYNC_SCRIPT)], cwd=str(ROOT))
    if r.returncode != 0:
        return r.returncode

    # 2. drop round-1 transparent alphas for codes about to be reprocessed.
    # Without this, modal_postprocess.py's idempotent skip-if-exists guard
    # would leave the old alphas in place.
    print(f"\n=== 2/3: drop {len(codes)} stale alphas in icons/ ===")
    n_dropped = 0
    n_missing = 0
    for c in codes:
        p = ICONS / f"{c}.png"
        if p.exists():
            p.unlink()
            n_dropped += 1
        else:
            n_missing += 1
    print(f"  dropped: {n_dropped}, already missing: {n_missing}")

    if args.no_modal:
        print(f"\n--no-modal set; skipping modal step. Run yourself:")
        print(f"  modal run modal_postprocess.py")
        return 0

    # 3. modal bg-removal — processes only the missing icons/, ~5 min on A10G.
    # Invoked as `python -m modal` because the `modal` script isn't always
    # on PATH from a subprocess context on Windows.
    print(f"\n=== 3/3: modal run modal_postprocess.py ===")
    r = subprocess.run([sys.executable, "-m", "modal", "run",
                        str(MODAL_SCRIPT)], cwd=str(ROOT))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
