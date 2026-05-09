#!/usr/bin/env python3
"""Copy production PNGs from act-img-gen/output/comic_v4/<SBLS>/ into icons/<SBLS>.png.

Looks at the canonical (already bg-removed) file:
    ../act-img-gen/output/comic_v4/<SBLS>/openai__gpt-image-2-low.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_ROOT = ROOT.parent / "act-img-gen" / "output" / "comic_v4"
DST = ROOT / "icons"           # transparent (production)
DST_RAW = ROOT / "icons_raw"   # white-background original
SLUG = "openai__gpt-image-2-low"


def _copy_if_newer(src: Path, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return False
    shutil.copy2(src, dst)
    return True


def main() -> None:
    if not SRC_ROOT.exists():
        raise SystemExit(f"Source missing: {SRC_ROOT}")
    DST.mkdir(exist_ok=True)
    DST_RAW.mkdir(exist_ok=True)
    counts = {"alpha": 0, "raw": 0, "raw_from_canonical": 0, "skipped": 0}

    for item_dir in sorted(SRC_ROOT.iterdir()):
        if not item_dir.is_dir() or item_dir.name.startswith("_"):
            continue
        canonical = item_dir / f"{SLUG}.png"
        raw = item_dir / f"{SLUG}_raw.png"
        sbls = item_dir.name

        if raw.exists():
            # Lokale bg-removal lief: canonical = transparent, _raw = weißer Hintergrund
            if _copy_if_newer(canonical, DST / f"{sbls}.png"):
                counts["alpha"] += 1
            else:
                counts["skipped"] += 1
            if _copy_if_newer(raw, DST_RAW / f"{sbls}.png"):
                counts["raw"] += 1
            else:
                counts["skipped"] += 1
        elif canonical.exists():
            # Keine lokale bg-removal: canonical IST der Raw mit weißem Hintergrund.
            # Nach icons_raw/ — wartet auf modal_postprocess.py oder
            # `generate.py --postprocess`.
            if _copy_if_newer(canonical, DST_RAW / f"{sbls}.png"):
                counts["raw_from_canonical"] += 1
            else:
                counts["skipped"] += 1

    print(f"Synced: alpha={counts['alpha']} raw={counts['raw']} "
          f"raw_only={counts['raw_from_canonical']} skipped={counts['skipped']}")


if __name__ == "__main__":
    main()
