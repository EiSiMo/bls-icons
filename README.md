# BLS 4.0 (Bundeslebensmittelschlüssel) Icon Set

In my project [ACP](https://git.moritz.run/moritz/act) (Adaptive Calorie Tracker) I am using the BLS Dataset for trhe nutritional information of generic German products. Unfortunatley I was missing clean, same-styled Icons for each of the 7140 individual entries. Thats why I used AI to genrate my own. If you need something like this too: here you go.

![100 random samples from the dataset](grid.png)

## Dataset

| | |
|---|---|
| BLS 4.0 entries covered | 7140 |
| Distinct icons | 4987 |
| Resolution | 1024×1024 PNG |
| `icons_raw/` | source images, white background |
| `icons/` | transparent (after background removal) |
| `items.csv` | one row per icon: `code, name_de, name_en` |
| `aliases.csv` | full mapping `bls_code → icon_code` (7140 rows) |

The 7140 → 4987 reduction collapses items that differ only by preparation
method (raw, cooked, fried, ...) onto a shared icon. Lookup at app runtime:

```python
icon_path = f"icons/{aliases[bls_code]}.png"
```

## Workflow

1. **Source.** BLS 4.0 Excel parsed via `openpyxl`, 7140 items.
2. **Deduplication.** Regex-strip preparation suffixes from item names → 4987
   canonical icons. `aliases.csv` keeps the full mapping so any BLS code can
   resolve to its icon.
3. **Prompt generation.** Per item, `gpt-5-mini` reads the German + English name
   and the style spec (`comic_v4.md` in [act-img-gen](https://git.moritz.run/moritz/act-img-gen))
   and returns an image prompt. Sync API call, ~3 cents per 100 items.
4. **Image generation.** `gpt-image-2` at quality `low`, 1024×1024, via the
   OpenAI Batch API (50% discount, async with 24h window). Output is a PNG with
   white background. ~$22 for the full 4987-item run.
5. **Background removal (optional).** `BiRefNet-massive` via the `rembg` library,
   run on Modal serverless **T4 GPUs**. ~$0.70 and ~30 min wall time for 4987
   icons. Local Pascal-class GPUs (e.g. GTX 1080) hit cuDNN-frontend issues, hence
   the Modal route.
6. **Output.** White-bg PNGs in `icons_raw/`, transparent PNGs in `icons/`.

The orchestrator `run_pipeline.py` chains all steps with retries, idempotent
resume, full logging to `pipeline.log`, and synchronous regeneration for items
that hit OpenAI's image moderation (raw animal products occasionally trigger
false-positive `safety_violations=[sexual]`).

## Models

| Step | Model | Mode | Cost (full run) |
|---|---|---|---|
| Prompter | `gpt-5-mini` (`reasoning_effort=minimal`) | sync | ~$1 |
| Image gen | `gpt-image-2` quality `low` | OpenAI Batch | ~$22 |
| Background removal | `BiRefNet-massive` (rembg) | Modal T4 GPU | ~$0.70 |

## Storage

Icons are stored via **Git LFS** (`*.png` in `icons/` and `icons_raw/`). A plain
`git clone` only fetches the small text/CSV files; binaries arrive on first
checkout (or `git lfs pull`). The repo itself stays small enough to clone in
seconds.
