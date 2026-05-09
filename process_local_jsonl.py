"""Process a locally-downloaded batch output JSONL (streaming, no full load).

The fetch step in generate.py loads the entire 6.3GB output into memory, which
crashes Python and also fails because Cloudflare 504s on download. Workaround:
download once via the OpenAI website, then run this script to extract all PNGs
from the local file in a streaming pass.

Usage:
    python process_local_jsonl.py <jsonl_path>
    python process_local_jsonl.py "C:/Users/moritz/Downloads/batch_..._output.jsonl"
"""
from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

ACTGEN = Path("C:/Users/moritz/Documents/act-img-gen")
OUTPUT = ACTGEN / "output" / "comic_v4"
BATCHES = OUTPUT / "_batches"
SLUG = "openai__gpt-image-2-low"
MODEL_ID = "openai/gpt-image-2@low"
EST_IMAGE_COST = 0.0033


def detect_dimensions(b: bytes) -> tuple[int, int]:
    if b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        return struct.unpack(">II", b[16:24])
    return (0, 0)


def cost_from_usage(usage: dict) -> float:
    inp = usage.get("input_tokens", 0) * 5e-6
    out = usage.get("output_tokens", 0) * 4e-5
    return inp + out


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_jsonl_path>")
        return 1
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"NOT FOUND: {src}")
        return 1

    size_gb = src.stat().st_size / 1024**3
    print(f"Source:  {src}  ({size_gb:.2f} GB)")
    print(f"Target:  {OUTPUT}")
    print()

    # Try to extract batch_id from filename for manifest tagging
    batch_id = "batch_unknown"
    for part in src.stem.split("_"):
        if part.startswith("batch") and len(part) > 10:
            pass
    if "batch_" in src.name:
        batch_id = src.name.split("_output")[0]
    print(f"Batch-ID: {batch_id}")
    print()

    saved = errors = warned = 0
    cost_total = 0.0
    line_no = 0

    with src.open("rb") as f:
        for raw_line in f:
            line_no += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  L{line_no}: parse error: {e}")
                errors += 1
                continue

            sbls = rec.get("custom_id")
            if not sbls:
                continue
            item_dir = OUTPUT / sbls
            manifest_path = item_dir / "manifest.json"
            if not manifest_path.exists():
                print(f"  WARN {sbls}: kein manifest, skip")
                warned += 1
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  {sbls} manifest read err: {e}")
                errors += 1
                continue

            response = rec.get("response") or {}
            body = response.get("body") or {}
            status = response.get("status_code", 0)

            if rec.get("error") or status >= 400:
                err = rec.get("error") or body
                print(f"  {sbls} ERR (HTTP {status}): {json.dumps(err)[:160]}")
                manifest.setdefault("image_results", []).append({
                    "model": MODEL_ID, "batch_id": batch_id,
                    "mode": "batch", "error": json.dumps(err),
                })
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, ensure_ascii=False),
                    encoding="utf-8")
                errors += 1
                continue

            data = body.get("data") or []
            if not data:
                print(f"  {sbls} ERR: leeres data-Array")
                errors += 1
                continue

            img_b64 = data[0].get("b64_json")
            if not img_b64:
                print(f"  {sbls} ERR: kein b64_json")
                errors += 1
                continue

            raw_png = base64.b64decode(img_b64)
            w, h = detect_dimensions(raw_png)
            usage = body.get("usage") or {}
            cost = cost_from_usage(usage) if usage else EST_IMAGE_COST

            file = f"{SLUG}.png"
            (item_dir / file).write_bytes(raw_png)

            manifest["image_results"] = [r for r in manifest.get("image_results", [])
                                         if r.get("file") != file]
            manifest["image_results"].append({
                "model": MODEL_ID, "file": file,
                "width": w, "height": h,
                "cost_usd": cost, "usage": usage,
                "batch_id": batch_id, "mode": "batch",
            })
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8")

            saved += 1
            cost_total += cost

            if saved % 200 == 0:
                print(f"  {saved} saved …", flush=True)

    print()
    print(f"=== Done ===")
    print(f"  Saved:   {saved}")
    print(f"  Errors:  {errors}")
    print(f"  Warned:  {warned}")
    print(f"  Lines:   {line_no}")
    print(f"  Cost:    ${cost_total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
