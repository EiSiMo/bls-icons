#!/usr/bin/env python3
"""Build a static index.html viewing every PNG in icons/ as a tight thumbnail grid.

Scans icons/ for *.png, joins with items.csv (code,name_de,name_en) for labels,
embeds the data inline so the page works without a server.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
ICONS = ROOT / "icons"
ITEMS_CSV = ROOT / "items.csv"
OUT = ROOT / "index.html"


def main() -> None:
    names: dict[str, tuple[str, str]] = {}
    if ITEMS_CSV.exists():
        with ITEMS_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                names[r["code"]] = (r["name_de"], r["name_en"])

    # Auto-detect available variants — each rendered as its own toggle in the viewer
    variants: list[tuple[str, str]] = []
    for label, dirname in [
        ("alpha", "icons"),
        ("modal", "icons_modal"),
        ("raw", "icons_raw"),
    ]:
        if (ROOT / dirname).is_dir() and any((ROOT / dirname).glob("*.png")):
            variants.append((label, dirname))

    if not variants:
        raise SystemExit("No icon folders found (icons/, icons_modal/, icons_raw/)")

    # Use the first variant's PNGs as the canonical item list
    primary_dir = ROOT / variants[0][1]
    pngs = sorted(primary_dir.glob("*.png"))
    items = []
    for p in pngs:
        code = p.stem
        de, en = names.get(code, (code, ""))
        items.append({"c": code, "d": de, "e": en})

    data_json = json.dumps(items, ensure_ascii=False)
    variants_json = json.dumps(variants, ensure_ascii=False)
    html = (TEMPLATE
            .replace("__DATA__", data_json)
            .replace("__VARIANTS__", variants_json)
            .replace("__N__", str(len(items))))
    OUT.write_text(html, encoding="utf-8")
    variant_names = ", ".join(f"{l}({d})" for l, d in variants)
    print(f"Wrote {OUT}  ({len(items)} icons, variants: {variant_names})")


TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>BLS Icons (__N__)</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px/1.4 system-ui, sans-serif;
         background: #1a1a1a; color: #e5e5e5; }
  header { padding: 8px 12px; background: #111; border-bottom: 1px solid #333;
           display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
           position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 14px; font-weight: 600; }
  header .count { color: #888; }
  header input { background: #222; color: #eee; border: 1px solid #444;
                 padding: 4px 8px; border-radius: 4px; min-width: 240px; }
  header label { display: flex; align-items: center; gap: 6px; color: #aaa; }
  header input[type=range] { width: 120px; }
  header .variants { display: flex; gap: 4px; }
  header .variants button { background: #222; color: #ccc; border: 1px solid #444;
                            padding: 4px 10px; border-radius: 4px; cursor: pointer;
                            font: inherit; }
  header .variants button.active { background: #4a90e2; color: #fff; border-color: #4a90e2; }
  header .variants button:hover:not(.active) { background: #2a2a2a; }

  .grid { display: grid;
          grid-template-columns: repeat(auto-fill, minmax(var(--size, 80px), 1fr));
          gap: 4px; padding: 8px; }
  .cell { position: relative; aspect-ratio: 1; background:
          repeating-conic-gradient(#2a2a2a 0% 25%, #232323 0% 50%) 0 0/16px 16px;
          border-radius: 3px; overflow: hidden; cursor: zoom-in; }
  .cell img { width: 100%; height: 100%; object-fit: contain;
              display: block; image-rendering: -webkit-optimize-contrast; }
  .cell .label { position: absolute; left: 0; right: 0; bottom: 0;
                 background: rgba(0,0,0,0.75); color: #fff;
                 font-size: 10px; padding: 2px 4px;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                 opacity: 0; transition: opacity 0.1s; pointer-events: none; }
  .cell:hover .label { opacity: 1; }
  body.show-labels .cell .label { opacity: 1; }

  /* Lightbox */
  .lb { display: none; position: fixed; inset: 0;
        background: rgba(0,0,0,0.92); z-index: 100;
        align-items: center; justify-content: center; flex-direction: column;
        padding: 20px; gap: 12px; cursor: zoom-out; }
  .lb.open { display: flex; }
  .lb img { max-width: 90vw; max-height: 80vh; object-fit: contain;
            background: repeating-conic-gradient(#444 0% 25%, #333 0% 50%) 0 0/24px 24px;
            border-radius: 6px; }
  .lb .meta { color: #ddd; font-size: 14px; text-align: center; max-width: 80vw; }
  .lb .meta .code { color: #888; font-family: ui-monospace, monospace; }
  .lb .meta .en { color: #999; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>BLS Icons</h1>
  <span class="count" id="count">0</span>
  <span class="variants" id="variants"></span>
  <input type="search" id="q" placeholder="Suche (Code oder Name) …">
  <label>Größe <input type="range" id="size" min="40" max="240" value="80"></label>
  <label><input type="checkbox" id="lbl"> Namen immer zeigen</label>
</header>
<main class="grid" id="grid"></main>
<div class="lb" id="lb">
  <img id="lb-img" alt="">
  <div class="meta" id="lb-meta"></div>
</div>
<script>
const ITEMS = __DATA__;
const VARIANTS = __VARIANTS__;
let activeVariant = VARIANTS[0][1];

const grid = document.getElementById('grid');
const q = document.getElementById('q');
const size = document.getElementById('size');
const lbl = document.getElementById('lbl');
const count = document.getElementById('count');
const variantBar = document.getElementById('variants');

if (VARIANTS.length > 1) {
  variantBar.innerHTML = VARIANTS.map(([label, dir]) =>
    `<button data-dir="${dir}">${label}</button>`).join('');
  variantBar.querySelectorAll('button').forEach(b => {
    if (b.dataset.dir === activeVariant) b.classList.add('active');
    b.addEventListener('click', () => {
      activeVariant = b.dataset.dir;
      variantBar.querySelectorAll('button').forEach(x =>
        x.classList.toggle('active', x.dataset.dir === activeVariant));
      render(q.value);
    });
  });
}

function render(filter='') {
  const f = filter.trim().toLowerCase();
  const html = ITEMS.filter(it => !f || it.c.toLowerCase().includes(f)
                              || it.d.toLowerCase().includes(f)
                              || (it.e || '').toLowerCase().includes(f))
    .map(it => `<div class="cell" data-c="${it.c}">
        <img src="${activeVariant}/${it.c}.png" loading="lazy" alt="${escapeHtml(it.d)}">
        <div class="label">${escapeHtml(it.d)}</div>
      </div>`).join('');
  grid.innerHTML = html;
  count.textContent = `${grid.children.length} / ${ITEMS.length}`;
}
function escapeHtml(s) { return s.replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

q.addEventListener('input', e => render(e.target.value));
size.addEventListener('input', e => document.documentElement.style
  .setProperty('--size', e.target.value + 'px'));
lbl.addEventListener('change', e => document.body.classList
  .toggle('show-labels', e.target.checked));

const lb = document.getElementById('lb');
const lbImg = document.getElementById('lb-img');
const lbMeta = document.getElementById('lb-meta');
grid.addEventListener('click', e => {
  const cell = e.target.closest('.cell');
  if (!cell) return;
  const it = ITEMS.find(x => x.c === cell.dataset.c);
  lbImg.src = `icons/${it.c}.png`;
  lbMeta.innerHTML = `<div class="code">${it.c}</div>
    <div>${escapeHtml(it.d)}</div>
    ${it.e ? `<div class="en">${escapeHtml(it.e)}</div>` : ''}`;
  lb.classList.add('open');
});
lb.addEventListener('click', () => lb.classList.remove('open'));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') lb.classList.remove('open');
});

render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
