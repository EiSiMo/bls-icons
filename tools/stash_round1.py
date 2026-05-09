"""Snapshot the round-1 icons (white-bg + BiRefNet alpha) for every code that
got regenerated in round 2, so review.py can show small thumbnails of "what
it looked like before" alongside the new round-2 panels.

Reads the LFS pointer at HEAD for each file, then copies the corresponding
blob from .git/lfs/objects/ into icons_round1_raw/ and icons_round1_alpha/.
No working-tree mutation, no git checkout dance.

Run after migrate_review_for_round2.py:
    python tools/stash_round1.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LFS_OBJECTS = ROOT / ".git" / "lfs" / "objects"
REVIEW_FILE = ROOT / "review.json"

DST_RAW = ROOT / "icons_round1_raw"
DST_ALPHA = ROOT / "icons_round1_alpha"

POINTER_OID = re.compile(r"^oid sha256:([a-f0-9]{64})$", re.MULTILINE)


def lfs_pointer_at_head(path: str) -> str | None:
    """Return the LFS pointer text for `path` at HEAD, or None if `path`
    isn't an LFS-tracked file at HEAD (e.g. file didn't exist yet)."""
    try:
        out = subprocess.check_output(
            ["git", "show", f"HEAD:{path}"], cwd=str(ROOT))
    except subprocess.CalledProcessError:
        return None
    text = out.decode("utf-8", errors="ignore")
    return text if "oid sha256:" in text else None


def lfs_blob_path(pointer: str) -> Path | None:
    m = POINTER_OID.search(pointer)
    if not m:
        return None
    sha = m.group(1)
    return LFS_OBJECTS / sha[:2] / sha[2:4] / sha


def stash_one(code: str) -> tuple[bool, bool]:
    """Return (raw_ok, alpha_ok)."""
    out = [False, False]
    for i, (src_path, dst_dir) in enumerate([
        (f"icons_raw/{code}.png", DST_RAW),
        (f"icons/{code}.png", DST_ALPHA),
    ]):
        ptr = lfs_pointer_at_head(src_path)
        if ptr is None:
            continue
        blob = lfs_blob_path(ptr)
        if blob is None or not blob.exists():
            continue
        dst = dst_dir / f"{code}.png"
        dst.write_bytes(blob.read_bytes())
        out[i] = True
    return tuple(out)  # type: ignore


def main() -> int:
    state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    codes = sorted(state.get("review_history", {}).keys())
    if not codes:
        print("review_history is empty — nothing to stash.")
        return 0

    DST_RAW.mkdir(exist_ok=True)
    DST_ALPHA.mkdir(exist_ok=True)
    print(f"{len(codes)} round-1 codes to stash …")

    n_raw = n_alpha = n_missing = 0
    for i, c in enumerate(codes, 1):
        raw_ok, alpha_ok = stash_one(c)
        if raw_ok:
            n_raw += 1
        if alpha_ok:
            n_alpha += 1
        if not (raw_ok and alpha_ok):
            n_missing += 1
        if i % 50 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)}  raw={n_raw} alpha={n_alpha}")

    print()
    print(f"Stashed: raw={n_raw}  alpha={n_alpha}  partial/missing={n_missing}")
    print(f"  → {DST_RAW.relative_to(ROOT)}/  {DST_ALPHA.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
