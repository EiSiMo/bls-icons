#!/usr/bin/env python3
"""Copy production PNGs from act-img-gen/output/comic_v4/<SBLS>/ into icons/<SBLS>.png.

Looks at the canonical (already bg-removed) file:
    ../act-img-gen/output/comic_v4/<SBLS>/openai__gpt-image-2-low.png

After the per-canonical sync, materializes the alias mapping from
aliases.csv: every bls_code that maps to a different icon_code is
written as a duplicate file (bls_code.png) so consumers can do a
direct icons/{bls_code}.png lookup without an alias table.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
SRC_ROOT = ROOT.parent / "act-img-gen" / "output" / "comic_v4"
DST = ROOT / "icons"           # transparent (production)
DST_RAW = ROOT / "icons_raw"   # white-background original
ALIASES = ROOT / "aliases.csv"
SLUG = "openai__gpt-image-2-low"


def _copy_if_newer(src: Path, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return False
    shutil.copy2(src, dst)
    return True


def expand_aliases() -> dict[str, int]:
    """Materialize aliases.csv into both icons/ and icons_raw/.

    For each (bls_code, icon_code) where bls_code != icon_code, copy
    {dir}/{icon_code}.png -> {dir}/{bls_code}.png if missing or stale.
    Idempotent: re-running only writes files whose source is newer.
    """
    counts = {"alpha": 0, "raw": 0, "skipped": 0, "missing_canonical": 0}
    if not ALIASES.exists():
        return counts
    with ALIASES.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bls = row["bls_code"]
            icon = row["icon_code"]
            if bls == icon:
                continue
            for d, key in ((DST, "alpha"), (DST_RAW, "raw")):
                src = d / f"{icon}.png"
                dst = d / f"{bls}.png"
                if not src.exists():
                    counts["missing_canonical"] += 1
                    continue
                if _copy_if_newer(src, dst):
                    counts[key] += 1
                else:
                    counts["skipped"] += 1
    return counts


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

    a = expand_aliases()
    print(f"Aliases materialized: alpha={a['alpha']} raw={a['raw']} "
          f"skipped={a['skipped']} missing_canonical={a['missing_canonical']}")


if __name__ == "__main__":
    main()
