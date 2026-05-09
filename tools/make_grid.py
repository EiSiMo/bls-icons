"""Render the README sample grid: 10 items per row, one row per BLS
Hauptgruppe, with the category label printed on the left edge.

The Hauptgruppe of an item is determined by the first letter of its
SBLS code. Each row picks `COLS` random items (seeded for
reproducibility) from that group, on a white or checkered background
depending on the variant.

  python tools/make_grid.py            # grid.png from icons_raw/ on white bg
  python tools/make_grid.py --alpha    # grid_alpha.png from icons/ on checkered bg
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

CELL = 200
COLS = 10
LABEL_W = 240
SEED = 42

# BLS Hauptgruppen — short German labels. Order matches BLS canonical.
HAUPTGRUPPEN: dict[str, str] = {
    "B": "Brot, Backwaren",
    "C": "Getreide, Mehle",
    "D": "Hülsenfrüchte",
    "E": "Eier",
    "F": "Obst, Säfte",
    "G": "Gemüse, Pilze",
    "H": "Milcherzeugnisse",
    "K": "Kartoffeln, Wurzeln",
    "M": "Milch",
    "N": "Erfrischungsgetränke",
    "P": "Alkoholische Getränke",
    "Q": "Öle, Fette",
    "R": "Salz, Würzmittel",
    "S": "Süßwaren, Zucker",
    "T": "Fisch",
    "U": "Fleisch",
    "V": "Wild, Innereien",
    "W": "Wurst, Geflügel",
    "X": "Zubereitete Speisen",
    "Y": "Mischgerichte",
}


def load_font(size: int) -> ImageFont.ImageFont:
    """Try a few Windows/Linux/Mac system fonts; fall back to default."""
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def checkered(w: int, h: int, sq: int = 20,
              c1=(255, 255, 255), c2=(220, 220, 220)) -> Image.Image:
    img = Image.new("RGB", (w, h), c1)
    draw = ImageDraw.Draw(img)
    for y in range(0, h, sq):
        for x in range(0, w, sq):
            if ((x // sq) + (y // sq)) % 2:
                draw.rectangle((x, y, x + sq, y + sq), fill=c2)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", action="store_true",
                    help="render transparent variant from icons/ on checkered bg")
    args = ap.parse_args()

    src_dir = (ROOT / "icons") if args.alpha else (ROOT / "icons_raw")
    out = (ROOT / "grid_alpha.png") if args.alpha else (ROOT / "grid.png")

    # Bucket icons by Hauptgruppe — first letter of the file stem.
    by_group: dict[str, list[Path]] = {}
    for p in sorted(src_dir.glob("*.png")):
        letter = p.stem[:1]
        if letter in HAUPTGRUPPEN:
            by_group.setdefault(letter, []).append(p)

    # Order rows in canonical BLS order, skipping any group that doesn't
    # actually have COLS icons (shouldn't happen with the current dataset).
    rows = [(letter, name) for letter, name in HAUPTGRUPPEN.items()
            if len(by_group.get(letter, [])) >= COLS]

    n_rows = len(rows)
    grid_w = LABEL_W + COLS * CELL
    grid_h = n_rows * CELL

    if args.alpha:
        # Label area solid white (more legible than checkered behind text);
        # image area gets the alpha checker.
        canvas = Image.new("RGB", (grid_w, grid_h), "white")
        cells_bg = checkered(COLS * CELL, grid_h)
        canvas.paste(cells_bg, (LABEL_W, 0))
    else:
        canvas = Image.new("RGB", (grid_w, grid_h), "white")

    draw = ImageDraw.Draw(canvas)
    font = load_font(20)

    rng = random.Random(SEED)
    for r, (letter, name) in enumerate(rows):
        candidates = by_group[letter]
        sample = rng.sample(candidates, COLS)
        y = r * CELL

        # Row label (left edge), vertically centered. Soft separator above
        # except for the first row, so categories visually delimit.
        if r > 0:
            draw.line((0, y, grid_w, y), fill=(230, 230, 230), width=1)
        text = f"{letter}  {name}"
        # Measure text height to center vertically in the cell.
        bbox = draw.textbbox((0, 0), text, font=font)
        th = bbox[3] - bbox[1]
        draw.text((20, y + (CELL - th) // 2 - bbox[1]),
                  text, fill=(50, 50, 50), font=font)

        for c, p in enumerate(sample):
            img = Image.open(p).resize((CELL, CELL), Image.LANCZOS)
            x = LABEL_W + c * CELL
            if args.alpha:
                rgba = img.convert("RGBA")
                canvas.paste(rgba, (x, y), rgba)
            else:
                canvas.paste(img.convert("RGB"), (x, y))

    canvas.save(out, optimize=True)
    print(f"wrote {out} ({grid_w}x{grid_h}, {n_rows} rows × {COLS} cols, "
          f"{out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
