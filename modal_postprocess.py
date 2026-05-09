"""Modal-based background removal for BLS icons.

Runs BiRefNet-massive on a Modal A10G GPU (Ampere, sm_86 — full cuDNN-Frontend
support, ~3× faster than T4 for BiRefNet inference). For ~5000 icons:
~$0.25 total, ~10–12 min wall time. T4 also works (cheaper hourly but
slower overall). A100-40GB gives another ~2× speedup if you want to splurge.

One-time setup:
    pip install modal
    modal token new        # opens browser, free $30 starter credits

Run:
    modal run modal_postprocess.py                                # icons_raw/ → icons/
    modal run modal_postprocess.py --in-dir foo --out-dir bar     # custom dirs

Idempotent: skips files that already exist in out-dir.
"""
from __future__ import annotations

import io
from pathlib import Path

import modal

app = modal.App("bls-bg-removal")


def _preload_model():
    """Run during image build to bake the BiRefNet ONNX (973 MB) into the
    container image. Without this, every cold-started worker would download
    1.5 GB before processing its first item."""
    from rembg import new_session
    new_session("birefnet-massive")


# Container image: CUDA 12.6 + cuDNN 9 base (matches onnxruntime-gpu 1.20+
# requirements). cuDNN 8 base would fail with libcudnn.so.9 not found.
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04",
        add_python="3.12",
    )
    .apt_install("libglib2.0-0", "libgl1")
    .pip_install(
        "rembg[gpu]",
        "Pillow",
        "numpy",
        "onnxruntime-gpu",
    )
    .run_function(_preload_model, gpu="A10G")
)


@app.cls(image=image, gpu="A10G", scaledown_window=120, timeout=900)
class BgRemover:
    @modal.enter()
    def load(self):
        from rembg import new_session
        self.session = new_session("birefnet-massive")

    @modal.method()
    def strip(self, png_bytes: bytes) -> bytes:
        from rembg import remove
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        rgba = remove(img, session=self.session).convert("RGBA")
        buf = io.BytesIO()
        rgba.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


@app.local_entrypoint()
def main(in_dir: str = "icons_raw", out_dir: str = "icons"):
    in_root = Path(in_dir)
    out_root = Path(out_dir)
    if not in_root.exists():
        raise SystemExit(f"input dir not found: {in_root}")
    out_root.mkdir(parents=True, exist_ok=True)

    all_pngs = sorted(in_root.glob("*.png"))
    todo = [p for p in all_pngs if not (out_root / p.name).exists()]
    print(f"{len(todo)} to process, {len(all_pngs) - len(todo)} already done")
    if not todo:
        return

    payload = [p.read_bytes() for p in todo]
    remover = BgRemover()
    n = 0
    for src, alpha in zip(todo, remover.strip.map(payload)):
        (out_root / src.name).write_bytes(alpha)
        n += 1
        if n % 25 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}")
    print(f"Done. {n} images → {out_root}")
