"""Stage-1 of the round-2 pipeline.

For every item the reviewer marked `bg_choice=="pil"` in review.json (i.e.
PIL flood-fill produced a cleaner alpha than BiRefNet), recompute the PIL
flood-fill mask at full 1024 resolution from icons_raw/<code>.png and
overwrite icons/<code>.png with it. No image regen — the source image was
fine, only BiRefNet's mask was bad.

Run:
    python tools/swap_pil_alphas.py             # writes results
    python tools/swap_pil_alphas.py --dry-run   # print plan only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
REVIEW_FILE = ROOT / "review.json"
ICONS_RAW = ROOT / "icons_raw"
ICONS = ROOT / "icons"


def pil_flood_alpha(img: Image.Image, tol: int = 18) -> Image.Image:
    """Full-resolution PIL flood-fill alpha. Same logic as the preview path
    in tools/review.py but without the 512-px work-size shortcut, since
    these masks land in the published dataset."""
    src = img.convert("RGBA")
    w, h = src.size
    rgb = src.convert("RGB").copy()
    sentinel = (1, 2, 3)
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(rgb, seed, sentinel, thresh=tol * 3)
    r, g, b = rgb.split()
    mr = r.point(lambda v, t=sentinel[0]: 0 if v == t else 255)
    mg = g.point(lambda v, t=sentinel[1]: 0 if v == t else 255)
    mb = b.point(lambda v, t=sentinel[2]: 0 if v == t else 255)
    mask = ImageChops.lighter(ImageChops.lighter(mr, mg), mb)
    out = src.copy()
    out.putalpha(mask)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not REVIEW_FILE.exists():
        sys.exit(f"missing {REVIEW_FILE}")
    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    codes = sorted(c for c, v in state.get("reviews", {}).items()
                   if v.get("bg_choice") == "pil")
    print(f"{len(codes)} PIL-better items to swap")
    if args.dry_run:
        for c in codes[:10]:
            print(f"  {c}")
        if len(codes) > 10:
            print(f"  ... +{len(codes) - 10}")
        return

    missing = []
    for i, code in enumerate(codes, 1):
        src = ICONS_RAW / f"{code}.png"
        dst = ICONS / f"{code}.png"
        if not src.exists():
            missing.append(code)
            continue
        out = pil_flood_alpha(Image.open(src))
        out.save(dst, optimize=True)
        if i % 25 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)}")

    if missing:
        print(f"WARNING: {len(missing)} items had no icons_raw/<code>.png:")
        for c in missing[:10]:
            print(f"  {c}")
    print(f"Done. Overwrote {len(codes) - len(missing)} files in {ICONS}/")


if __name__ == "__main__":
    main()
