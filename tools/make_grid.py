"""Generate 10x10 sample grids for the README.

Picks 100 random items (seeded for reproducibility), resizes to 200x200,
and pastes into a single 2000x2000 PNG.

  python tools/make_grid.py            # grid.png from icons_raw/ on white bg
  python tools/make_grid.py --alpha    # grid_alpha.png from icons/ on checkered bg
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]  # repo root

CELL = 200
N = 10
SEED = 42


def checkered(size: int, square: int = 20,
              c1: tuple = (255, 255, 255), c2: tuple = (220, 220, 220)) -> Image.Image:
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)
    for y in range(0, size, square):
        for x in range(0, size, square):
            if ((x // square) + (y // square)) % 2:
                draw.rectangle((x, y, x + square, y + square), fill=c2)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", action="store_true",
                    help="render transparent variant from icons/ on checkered bg")
    args = ap.parse_args()

    if args.alpha:
        src_dir = ROOT / "icons"
        out = ROOT / "grid_alpha.png"
        grid = checkered(CELL * N)
    else:
        src_dir = ROOT / "icons_raw"
        out = ROOT / "grid.png"
        grid = Image.new("RGB", (CELL * N, CELL * N), "white")

    icons = sorted(src_dir.glob("*.png"))
    if len(icons) < N * N:
        raise SystemExit(f"need {N*N} icons in {src_dir}, got {len(icons)}")
    random.seed(SEED)
    sample = random.sample(icons, N * N)

    for i, p in enumerate(sample):
        img = Image.open(p).resize((CELL, CELL), Image.LANCZOS)
        x, y = (i % N) * CELL, (i // N) * CELL
        if args.alpha:
            rgba = img.convert("RGBA")
            grid.paste(rgba, (x, y), rgba)
        else:
            grid.paste(img.convert("RGB"), (x, y))

    grid.save(out, optimize=True)
    print(f"wrote {out} ({CELL*N}x{CELL*N}, {out.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
