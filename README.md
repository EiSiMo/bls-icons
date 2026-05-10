# BLS 4.0 (Bundeslebensmittelschlüssel) Icon Set

In my project [ACP](https://git.moritz.run/moritz/act) (Adaptive Calorie Tracker) I am using the BLS Dataset for the nutritional information of generic German products. Unfortunatley I was missing clean, same-styled Icons for each of the 7140 individual entries. Thats why I used AI to genrate my own. If you need something like this too: here you go.

10 random samples per BLS Hauptgruppe, one row per category:

![Sample grid, 10 items per BLS Hauptgruppe](grid.png)

Same items with backgrounds removed (the checker is just to show the alpha
— the actual files are transparent):

![Sample grid, transparent variant](grid_alpha.png)

> **Mirror notice.** This repo lives canonically at
> [git.moritz.run/moritz/bls-icons](https://git.moritz.run/moritz/bls-icons)
> with all icons stored via Git LFS. The
> [GitHub mirror](https://github.com/EiSiMo/bls-icons) carries the code and
> metadata only — clone from the canonical source if you want the binary
> images.

## Use it in your app

```bash
git clone ssh://git@git.moritz.run:2222/moritz/bls-icons.git
cd bls-icons
git lfs pull        # download all 7140 PNGs (~6.4 GB working tree)
```

```python
icon_path = f"icons/{bls_code}.png"           # transparent
icon_path = f"icons_raw/{bls_code}.png"       # white background
```

Every BLS 4.0 code resolves to a file directly — no alias lookup, no
preprocessing. Without `git lfs pull` you only get the metadata
(~23 MB, clones in seconds).

## Dataset

| | |
|---|---|
| BLS 4.0 codes covered | 7140 |
| Files in `icons/` and `icons_raw/` | 7140 each |
| Distinct images | 4987 |
| Resolution | 1024×1024 PNG |
| `icons_raw/` | source images, white background |
| `icons/` | transparent (after background removal) |
| `items.csv` | 4987 canonical entries: `code, name_de, name_en` |
| `aliases.csv` | 7140 `bls_code → icon_code` map (informational; lookup is no longer required) |

The 7140 → 4987 reduction collapses items that differ only by preparation
method (raw, cooked, fried, …) onto the same image. v3 materializes each
duplicate as its own file, so callers can `icons/{bls_code}.png`
directly. Server-side LFS storage stays at ~4.5 GB because identical
blobs are content-addressed; only the working tree grows to ~6.4 GB.

## How it was made

1. **Source.** BLS 4.0 Excel parsed via `openpyxl`, 7140 items.
2. **Deduplication.** Regex-strip preparation suffixes from item names →
   4987 canonical icons. `aliases.csv` keeps the full mapping.
3. **Prompt generation.** Per item, `gpt-5-mini` reads the German + English
   name and the style spec (`comic_v4.md` in
   [act-img-gen](https://git.moritz.run/moritz/act-img-gen)) and returns
   an image prompt. Sync API call, ~3 cents per 100 items.
4. **Image generation.** `gpt-image-2` at quality `low`, 1024×1024, via
   the OpenAI Batch API (50 % discount, async with 24 h window). Output
   is a PNG with white background. ~$22 for the full 4987-item run.
5. **Background removal.** `BiRefNet-massive` via the `rembg` library,
   run on Modal serverless A10G GPUs. ~$0.25 and ~10–12 min wall time
   for 4987 icons.
6. **Alias materialization.** `tools/sync_icons.py` expands `aliases.csv`
   into the 7140-file working tree.

The orchestrator `run_pipeline.py` chains all steps with retries,
idempotent resume, full logging to `pipeline.log`, and synchronous
regeneration for items that hit OpenAI's image moderation (raw animal
products occasionally trigger false-positive `safety_violations=[sexual]`).

After the initial generation, every icon was hand-reviewed and ~22 % were
refined: 489 alpha-mask swaps where naïve PIL flood-fill beat BiRefNet,
612 prompt rewrites + image regens informed by per-item reviewer feedback
(across two rounds), 16 white-powder items (flour, starch, milk powder)
collapsed onto a single canonical image to avoid the BiRefNet-on-white
mask issue, plus a handful of hand-picked swaps and reverts. Total
refinement cost: ~$2.66.

Earlier dataset shapes are preserved as tags: `v1.0` (pre-refinement,
4987 files), `v2.0` (post-review, 4987 files with alias lookup), `v3.0`
(this version, 7140 files with aliases pre-resolved).

## Regenerate

```bash
# companion repo: prompter + image-gen + style spec
git clone ssh://git@git.moritz.run:2222/moritz/act-img-gen.git ../act-img-gen

# local deps
pip install -r requirements.txt
pip install -r ../act-img-gen/requirements.txt

# OpenAI key for prompter + image gen
echo "OPENAI_API_KEY=sk-..." > ../act-img-gen/.env

# end-to-end run (~$22 + ~$0.25 Modal, completes in <24 h)
python run_pipeline.py
```

## Models

| Step | Model | Mode | Approx. cost (full 4987-item run) |
|---|---|---|---|
| Prompter | `gpt-5-mini` (`reasoning_effort=minimal`) | sync | ~$1 |
| Image gen | `gpt-image-2` quality `low` | OpenAI Batch | ~$22 |
| Background removal | `BiRefNet-massive` (rembg) | Modal A10G GPU | ~$0.25 |

## Repo layout

```
.
├── items.csv               4987 canonical icons (code, name_de, name_en)
├── aliases.csv             7140 bls_code → icon_code mapping (informational)
├── run_pipeline.py         end-to-end orchestrator (entry point)
├── modal_postprocess.py    Modal entry point for background removal
├── grid.png                README preview (regenerable via tools/make_grid.py)
├── grid_alpha.png          transparent variant of the same grid
├── icons_raw/              white-bg PNGs, one per BLS code (LFS, 7140 files)
├── icons/                  transparent PNGs, one per BLS code (LFS, 7140 files)
└── tools/
    ├── sync_icons.py       copies act-img-gen output → icons_raw/, expands aliases
    ├── fix_missing.py      sync regen for items missing from icons_raw/
    └── make_grid.py        regenerates grid.png / grid_alpha.png
```

## Storage

Icons are stored via **Git LFS** (`*.png` in `icons/` and `icons_raw/`).
A plain `git clone` only fetches the small text/CSV files; binaries
arrive on first checkout (or `git lfs pull`). The repo itself stays
small enough to clone in seconds.

LFS deduplicates identical blobs by SHA-256 on the server, so the 7140
files materialize from only ~4987 unique objects (~4.5 GB). The working
tree on disk after `git lfs pull` is ~6.4 GB because each file is
checked out as a real copy.

## License

Released into the public domain under [CC0 1.0](LICENSE). Use, modify, and
redistribute the icons, code, and metadata for any purpose without attribution.
