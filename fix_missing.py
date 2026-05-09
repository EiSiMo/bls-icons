"""Fix items that are in items.csv but missing from icons_raw/.

Three reasons for missing:
- moderation_blocked (manifest has the error)
- 502 during prompter (no manifest)
- never made it into the batch for any other reason

For each missing code: ensure dir exists, run prompter (with current Rule F),
sync image-generation call, save PNG + manifest. Sync sync_icons.py at the end.
"""
from __future__ import annotations

import base64
import csv
import importlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACT = ROOT.parent / "act-img-gen"
OUTPUT = ACT / "output" / "comic_v4"
ICONS_RAW = ROOT / "icons_raw"
ITEMS_CSV = ROOT / "items.csv"
SLUG = "openai__gpt-image-2-low"


def main() -> int:
    items = list(csv.DictReader(ITEMS_CSV.open(encoding="utf-8")))
    have = {p.stem for p in ICONS_RAW.glob("*.png")}
    missing = [r for r in items if r["code"] not in have]
    print(f"items.csv: {len(items)}   icons_raw: {len(have)}   missing: {len(missing)}")
    if not missing:
        return 0
    for r in missing:
        print(f"  {r['code']}  {r['name_de']}")
    print()

    sys.path.insert(0, str(ACT))
    g = importlib.import_module("generate")
    g.load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY missing")
        return 1
    style_spec = (ACT / "style" / "comic_v4.md").read_text(encoding="utf-8").strip()

    import requests
    OPENAI_BASE = "https://api.openai.com/v1"
    IMAGE_MODEL = "gpt-image-2"
    QUALITY = "low"

    fixed = 0
    for r in missing:
        sbls = r["code"]
        item = {"sbls": sbls, "name_de": r["name_de"], "name_en": r["name_en"]}
        item_dir = OUTPUT / sbls
        item_dir.mkdir(parents=True, exist_ok=True)

        for attempt in range(3):
            print(f"\n[{sbls}] attempt {attempt+1}: prompter ...", flush=True)
            try:
                llm = g.call_llm(api_key, style_spec, item)
            except Exception as e:
                print(f"  prompter ERR: {e} — retry in 10s")
                time.sleep(10)
                continue
            prompt = llm["text"]
            print(f"  prompt ok ({len(prompt)} chars), generating image ...", flush=True)
            (item_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")

            mf = item_dir / "manifest.json"
            if mf.exists():
                m = json.loads(mf.read_text(encoding="utf-8"))
            else:
                m = {"sbls": sbls, "name_de": r["name_de"], "name_en": r["name_en"],
                     "style": "comic_v4", "image_results": []}
            m["llm"] = llm
            m["image_results"] = [x for x in m.get("image_results", [])
                                  if not ("moderation_blocked" in x.get("error", "")
                                          or "safety_violations" in x.get("error", ""))]
            mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

            try:
                resp = requests.post(
                    f"{OPENAI_BASE}/images/generations",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"model": IMAGE_MODEL, "prompt": prompt,
                          "size": "1024x1024", "quality": QUALITY, "n": 1},
                    timeout=300,
                )
            except Exception as e:
                print(f"  image-gen HTTP ERR: {e}")
                time.sleep(5)
                continue

            if not resp.ok:
                body = resp.text[:300]
                print(f"  image-gen HTTP {resp.status_code}: {body}")
                if "moderation_blocked" in body or "safety_violations" in body:
                    m["image_results"].append({
                        "model": f"openai/{IMAGE_MODEL}@{QUALITY}",
                        "mode": "sync_regen", "error": body,
                    })
                    mf.write_text(json.dumps(m, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
                    print("  retrying with fresh prompt …")
                    continue
                # Other HTTP error: stop trying
                break

            data = resp.json()
            if not data.get("data"):
                print("  empty response")
                continue
            raw = base64.b64decode(data["data"][0]["b64_json"])
            (item_dir / f"{SLUG}.png").write_bytes(raw)
            shutil.copy2(item_dir / f"{SLUG}.png", ICONS_RAW / f"{sbls}.png")
            usage = data.get("usage", {})
            m["image_results"].append({
                "model": f"openai/{IMAGE_MODEL}@{QUALITY}",
                "file": f"{SLUG}.png", "usage": usage, "mode": "sync_regen",
                "regen_attempt": attempt + 1,
            })
            mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  OK -> {sbls}.png")
            fixed += 1
            break

    print(f"\nFixed {fixed}/{len(missing)}")
    return 0 if fixed == len(missing) else 1


if __name__ == "__main__":
    sys.exit(main())
