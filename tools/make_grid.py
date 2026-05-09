"""Generate a 10x10 sample grid from icons_raw/ for the README.

Picks 100 random items (seeded for reproducibility), resizes to 200x200,
and pastes into a single 2000x2000 PNG.
"""
from __future__ import annotations

import random
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]  # repo root
SRC = ROOT / "icons_raw"
OUT = ROOT / "grid.png"

CELL = 200
N = 10
SEED = 42


def main() -> None:
    icons = sorted(SRC.glob("*.png"))
    if len(icons) < N * N:
        raise SystemExit(f"need {N*N} icons, got {len(icons)}")
    random.seed(SEED)
    sample = random.sample(icons, N * N)

    grid = Image.new("RGB", (CELL * N, CELL * N), "white")
    for i, p in enumerate(sample):
        img = Image.open(p).convert("RGB").resize((CELL, CELL), Image.LANCZOS)
        grid.paste(img, ((i % N) * CELL, (i // N) * CELL))
    grid.save(OUT, optimize=True)
    print(f"wrote {OUT} ({CELL*N}x{CELL*N}, {OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
