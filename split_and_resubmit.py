"""Plan B: re-submit in N chunks if the 6.3GB output download keeps failing.

Splits items.csv into N parts, writes each as items_chunkXX.csv next to it,
and prints the submit commands. Each chunk's output will be ~6.3GB / N — small
enough to download. Manifests already exist for all items (from the failed run),
so the LLM prompter will be skipped (cache hit). Only image generation is paid
again (~$22 / N per chunk).

Usage:
    python split_and_resubmit.py --chunks 5      # just split
    python split_and_resubmit.py --chunks 5 --submit  # split + auto-submit all
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ITEMS = ROOT / "items.csv"
ACTGEN = ROOT.parent / "act-img-gen"
PYTHON = sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=5)
    ap.add_argument("--submit", action="store_true",
                    help="auch direkt submitten (sonst nur die CSVs schreiben)")
    args = ap.parse_args()

    rows = list(csv.DictReader(ITEMS.open(encoding="utf-8")))
    n = len(rows)
    sz = -(-n // args.chunks)  # ceil
    print(f"{n} items -> {args.chunks} chunks of <= {sz}")

    chunk_files = []
    for i in range(args.chunks):
        part = rows[i * sz:(i + 1) * sz]
        if not part:
            break
        path = ROOT / f"items_chunk{i+1:02d}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "name_de", "name_en"])
            w.writeheader()
            for r in part:
                w.writerow(r)
        chunk_files.append((path, len(part)))
        print(f"  wrote {path.name}  ({len(part)} items)")

    print()
    print("Submit-Commands:")
    for path, count in chunk_files:
        print(f"  python {ACTGEN}/generate.py --items-csv {path} -y "
              f"# {count} items, ~${count * 0.0044:.2f}")

    if not args.submit:
        return 0

    print()
    print("=== Auto-Submit ===")
    for path, count in chunk_files:
        print(f"\n>>> Submitting {path.name} ({count} items)")
        cmd = [PYTHON, "-X", "utf8", str(ACTGEN / "generate.py"),
               "--items-csv", str(path), "-y"]
        rc = subprocess.call(cmd, cwd=str(ACTGEN))
        if rc != 0:
            print(f"  FAILED rc={rc} — abort")
            return rc
    print("\nAlle Chunks submitted.")
    print("Status mit: curl https://api.openai.com/v1/batches  (oder im Dashboard)")
    print("Wenn alle 'completed': pro Chunk fetch + sync wie gewohnt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
