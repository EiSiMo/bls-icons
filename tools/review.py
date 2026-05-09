"""Interactive review tool for the BLS icon dataset.

Walks through every icon, shows the prompt + three rendered variants side by
side (PIL flood-fill alpha, BiRefNet alpha, raw white-bg), and lets you mark:
  - which background-removal method came out better, or "both bad"
  - free-text feedback (regenerate reason, hallucinations, ...)

State is saved after every Next/Previous to a single JSON file so you can
quit and resume any time. Items flagged with feedback or "both bad" form the
queue that the regeneration pipeline will pick up.

Run:
    python tools/review.py

Single-file, only depends on Pillow (already in requirements.txt).
"""
from __future__ import annotations

import csv
import json
import sys
import tkinter as tk
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageTk


ROOT = Path(__file__).resolve().parents[1]
ITEMS_CSV = ROOT / "items.csv"
ICONS_RAW = ROOT / "icons_raw"
ICONS_ALPHA = ROOT / "icons"
ICONS_R1_RAW = ROOT / "icons_round1_raw"          # round-1 white-bg snapshot
ICONS_R1_ALPHA = ROOT / "icons_round1_alpha"      # round-1 BiRefNet snapshot
PROMPTS_ROOT = ROOT.parent / "act-img-gen" / "output" / "comic_v4"
REVIEW_FILE = ROOT / "review.json"

CELL = 360            # preview tile size
CHECK_SQ = 18         # checkered square size
SCHEMA_VERSION = 1
MASSIVE_LABEL = "BiRefNet-massive better"
PIL_LABEL = "PIL flood-fill better"


# --- bg-removal: PIL flood-fill from the four corners ---------------------- #

def pil_flood_alpha(img: Image.Image, tol: int = 18,
                    work_size: int = 512) -> Image.Image:
    """Flood-fill the white-ish background from the four corners and turn it
    transparent. The "naive" baseline to compare BiRefNet against — it eats
    holes that touch the border but is fast and predictable.

    Computed at `work_size` (default 512) for speed; the resulting alpha mask
    is upscaled NEAREST back to the source resolution so we still return a
    full-res RGBA. For a 4-panel preview this is plenty crisp.
    """
    src = img.convert("RGBA")
    w, h = src.size
    s = min(work_size, max(w, h))
    rgb_small = src.convert("RGB").resize((s, s), Image.LANCZOS)
    sentinel = (1, 2, 3)
    for seed in [(0, 0), (s - 1, 0), (0, s - 1), (s - 1, s - 1)]:
        ImageDraw.floodfill(rgb_small, seed, sentinel, thresh=tol * 3)
    # Vectorised mask derivation — split channels, compare to sentinel via
    # point ops, AND together. Avoids per-pixel Python loops.
    r, g, b = rgb_small.split()
    mr = r.point(lambda v, t=sentinel[0]: 0 if v == t else 255)
    mg = g.point(lambda v, t=sentinel[1]: 0 if v == t else 255)
    mb = b.point(lambda v, t=sentinel[2]: 0 if v == t else 255)
    mask_small = ImageChops.lighter(ImageChops.lighter(mr, mg), mb)  # any channel mismatch → 255
    mask = mask_small.resize((w, h), Image.NEAREST)
    out = src.copy()
    out.putalpha(mask)
    return out


# --- checkered backdrop ---------------------------------------------------- #

def checkered(size: int, sq: int = CHECK_SQ,
              c1=(255, 255, 255), c2=(220, 220, 220)) -> Image.Image:
    img = Image.new("RGB", (size, size), c1)
    d = ImageDraw.Draw(img)
    for y in range(0, size, sq):
        for x in range(0, size, sq):
            if ((x // sq) + (y // sq)) % 2:
                d.rectangle((x, y, x + sq, y + sq), fill=c2)
    return img


def composite_on_checker(rgba: Image.Image, size: int) -> Image.Image:
    bg = checkered(size)
    fg = rgba.resize((size, size), Image.LANCZOS).convert("RGBA")
    bg.paste(fg, (0, 0), fg)
    return bg


def fit_white(rgb_or_rgba: Image.Image, size: int) -> Image.Image:
    img = rgb_or_rgba.convert("RGB").resize((size, size), Image.LANCZOS)
    return img


# --- review state ---------------------------------------------------------- #

def load_state() -> dict:
    if REVIEW_FILE.exists():
        with open(REVIEW_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # forward-compat default
        data.setdefault("schema", SCHEMA_VERSION)
        data.setdefault("reviews", {})
        data.setdefault("review_history", {})
        data.setdefault("current_index", 0)
        return data
    return {"schema": SCHEMA_VERSION, "current_index": 0,
            "reviews": {}, "review_history": {}}


def save_state(state: dict) -> None:
    tmp = REVIEW_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(REVIEW_FILE)


def load_items() -> list[dict]:
    with open(ITEMS_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_prompt(code: str) -> str:
    p = PROMPTS_ROOT / code / "prompt.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    # graceful fallback — manifest sometimes has it under llm.text
    m = PROMPTS_ROOT / code / "manifest.json"
    if m.exists():
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
            return data.get("llm", {}).get("text", "") or "(no prompt found)"
        except Exception:
            pass
    return "(no prompt available — act-img-gen repo not cloned at ../act-img-gen?)"


# --- the GUI --------------------------------------------------------------- #

class ReviewApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BLS icon review")
        self.root.geometry("1700x980")
        self.root.configure(bg="#1a1a1a")

        all_items = load_items()
        self.code_to_row = {r["code"]: r for r in all_items}
        self.state = load_state()

        # Round-N mode auto-detect: if any code has past entries in
        # review_history but no current entry in `reviews`, it's pending
        # re-review (i.e. it was regenerated in the upcoming round and is
        # waiting for the reviewer). Items with both a history AND a current
        # review are *done* in the current round and shouldn't show up.
        history = self.state.get("review_history", {})
        reviews = self.state.get("reviews", {})
        pending = {c for c in history if c not in reviews}
        if pending:
            self.items = [r for r in all_items if r["code"] in pending]
            self.round_mode = True
        else:
            self.items = all_items
            self.round_mode = False
        self.codes = [r["code"] for r in self.items]

        # Position is persisted as a code (`current_code`, robust across
        # mode flips and items.csv reorderings) with a fallback to the
        # legacy `current_index` (always in global-items coordinates from
        # round 1). Resolve to an index in the *current* code list.
        self._global_codes = [r["code"] for r in all_items]
        target = self.state.get("current_code")
        if target is None:
            saved_idx = self.state.get("current_index", 0)
            if 0 <= saved_idx < len(self._global_codes):
                target = self._global_codes[saved_idx]
        if target in self.codes:
            self.idx = self.codes.index(target)
        else:
            self.idx = 0

        self._photo_refs: list = []  # keep image refs alive
        self._pil_cache: dict[str, Image.Image] = {}        # main image PIL flood
        self._pil_cache_r1: dict[str, Image.Image] = {}     # round-1 PIL flood

        self._build_ui()
        # Defer first render until Tk has mapped the window and computed real
        # widget sizes. Without this, _tile_size sees winfo_width()=1 on the
        # very first call and the images come out at the fallback ~280px
        # instead of filling their cells.
        self.root.after_idle(self.render)
        # Re-render when the window is resized (debounced, only when the
        # *image cell* actually changed size — ignore unrelated subwidget
        # configure events).
        self._last_tile = -1
        self._resize_after: Optional[str] = None
        self.img_massive.bind("<Configure>", self._on_resize)
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())

    # ---- layout ---------------------------------------------------------- #

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # --- header (slim — just meta + search; product name moved down to
        # sit directly above the middle image so the eye doesn't have to
        # travel) ---
        hdr = tk.Frame(self.root, bg="#1a1a1a")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 4))
        hdr.columnconfigure(0, weight=1)

        self.meta_lbl = tk.Label(hdr, text="", font=("Segoe UI", 11),
                                 fg="#aaa", bg="#1a1a1a", anchor="w")
        self.meta_lbl.grid(row=0, column=0, sticky="w")

        sf = tk.Frame(hdr, bg="#1a1a1a")
        sf.grid(row=0, column=1, sticky="e")
        tk.Label(sf, text="jump to:", fg="#888", bg="#1a1a1a",
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 6))
        self.search_entry = tk.Entry(sf, width=14, font=("Segoe UI", 11),
                                     bg="#2a2a2a", fg="#fff",
                                     insertbackground="#fff",
                                     relief="flat", bd=4)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda e: self.jump_to(self.search_entry.get()))
        tk.Button(sf, text="go", command=lambda: self.jump_to(self.search_entry.get()),
                  bg="#3a3a3a", fg="#fff", relief="flat",
                  font=("Segoe UI", 10), padx=10).pack(side="left", padx=(6, 0))

        # --- panels row (4 equal columns) ---
        panels = tk.Frame(self.root, bg="#1a1a1a")
        panels.grid(row=1, column=0, sticky="nsew", padx=20, pady=8)
        for c in range(4):
            panels.columnconfigure(c, weight=1, uniform="panel")
        panels.rowconfigure(0, weight=0)  # name row
        panels.rowconfigure(1, weight=1)  # image row (stretches)
        panels.rowconfigure(2, weight=0)  # round-1 thumbnails (only in round-N mode)
        panels.rowconfigure(3, weight=0)  # controls
        panels.rowconfigure(4, weight=0)  # both-bad

        self.panels = panels

        # row 0: big product name + inline Google-Images button, spanning the
        # three image columns so the name sits directly above the middle
        # image. The name stays centered (column 1 has weight=0 between two
        # equal-weight spacers); the button floats to its right.
        name_row = tk.Frame(panels, bg="#1a1a1a")
        name_row.grid(row=0, column=1, columnspan=3,
                      sticky="ew", pady=(0, 6))
        name_row.columnconfigure(0, weight=1)
        name_row.columnconfigure(1, weight=0)
        name_row.columnconfigure(2, weight=1)

        self.name_lbl = tk.Label(name_row, text="",
                                 font=("Segoe UI", 22, "bold"),
                                 fg="#fff", bg="#1a1a1a", justify="center")
        self.name_lbl.grid(row=0, column=1)

        self.google_btn = tk.Button(
            name_row, text="🔎  Google Images",
            command=self._google_current,
            bg="#2a3a55", fg="#fff", activebackground="#3a4a70",
            activeforeground="#fff",
            font=("Segoe UI", 10, "bold"), relief="flat",
            padx=14, pady=6, cursor="hand2")
        self.google_btn.grid(row=0, column=2, sticky="w", padx=(16, 0))

        # Round-N history hint: shown when this code has prior reviews in
        # review_history. Highlights what the reviewer flagged last time so
        # round-2 isn't blind. Empty + zero-height when no history.
        self.history_lbl = tk.Label(
            name_row, text="", font=("Segoe UI", 11, "italic"),
            fg="#ffb86b", bg="#1a1a1a", anchor="center", justify="center",
            wraplength=1280)
        self.history_lbl.grid(row=1, column=0, columnspan=3,
                              sticky="ew", pady=(2, 0))

        # column 0: prompt (spans rows 0+1+2 so it lines up with images
        # and the round-1 thumbnail strip below)
        self.prompt_box = tk.Text(panels, wrap="word", font=("Segoe UI", 10),
                                  bg="#222", fg="#ddd", relief="flat",
                                  padx=10, pady=10, height=20)
        self.prompt_box.grid(row=0, column=0, rowspan=3,
                             sticky="nsew", padx=4)
        self.prompt_box.config(state="disabled")

        # columns 1–3: image holders (row 1 now)
        self.img_pil = tk.Label(panels, bg="#222")
        self.img_pil.grid(row=1, column=1, sticky="nsew", padx=4)
        self.img_massive = tk.Label(panels, bg="#222")
        self.img_massive.grid(row=1, column=2, sticky="nsew", padx=4)
        self.img_raw = tk.Label(panels, bg="#222")
        self.img_raw.grid(row=1, column=3, sticky="nsew", padx=4)

        # row 2: small round-1 thumbnails — only populated in round-N mode
        # (when icons_round1_*/  exist). Each thumbnail label sits in a
        # bordered frame with a tiny "Round 1" caption above the leftmost.
        self.thumb_pil = tk.Label(panels, bg="#1a1a1a")
        self.thumb_pil.grid(row=2, column=1, sticky="n", padx=4, pady=(4, 0))
        self.thumb_massive = tk.Label(panels, bg="#1a1a1a")
        self.thumb_massive.grid(row=2, column=2, sticky="n", padx=4, pady=(4, 0))
        self.thumb_raw = tk.Label(panels, bg="#1a1a1a")
        self.thumb_raw.grid(row=2, column=3, sticky="n", padx=4, pady=(4, 0))

        # --- controls row (under each panel) ---
        # under prompt: nothing (column 0 stays empty)
        tk.Label(panels, text="", bg="#1a1a1a").grid(row=3, column=0)

        self.choice = tk.StringVar(value="massive")  # default: massive

        self.pil_radio = tk.Radiobutton(
            panels, text=PIL_LABEL, variable=self.choice, value="pil",
            bg="#1a1a1a", fg="#fff", activebackground="#1a1a1a",
            activeforeground="#fff", selectcolor="#444",
            font=("Segoe UI", 13, "bold"), pady=10,
            command=self._save_choice)
        self.pil_radio.grid(row=3, column=1, sticky="ew", padx=4, pady=8)

        self.massive_radio = tk.Radiobutton(
            panels, text=MASSIVE_LABEL, variable=self.choice, value="massive",
            bg="#1a1a1a", fg="#fff", activebackground="#1a1a1a",
            activeforeground="#fff", selectcolor="#444",
            font=("Segoe UI", 13, "bold"), pady=10,
            command=self._save_choice)
        self.massive_radio.grid(row=3, column=2, sticky="ew", padx=4, pady=8)

        # under raw: feedback box (row 3, col 3)
        fb_wrap = tk.Frame(panels, bg="#1a1a1a")
        fb_wrap.grid(row=3, column=3, sticky="nsew", padx=4, pady=4)
        fb_wrap.columnconfigure(0, weight=1)
        tk.Label(fb_wrap, text="feedback / regen notes",
                 bg="#1a1a1a", fg="#888", font=("Segoe UI", 9),
                 anchor="w").grid(row=0, column=0, sticky="ew")
        self.feedback_box = tk.Text(fb_wrap, wrap="word", height=4,
                                    font=("Segoe UI", 10),
                                    bg="#2a2a2a", fg="#fff",
                                    insertbackground="#fff",
                                    relief="flat", padx=8, pady=6)
        self.feedback_box.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # both-bad button: row 4, spans columns 1+2 (centered between radios)
        self.both_bad_btn = tk.Button(
            panels, text="✕  both bad — regenerate",
            command=self._toggle_both_bad,
            bg="#5a2222", fg="#fff", activebackground="#7a2a2a",
            activeforeground="#fff",
            font=("Segoe UI", 12, "bold"), relief="flat", pady=10)
        self.both_bad_btn.grid(row=4, column=1, columnspan=2,
                               sticky="ew", padx=40, pady=(0, 4))

        # --- bottom bar: nav (Prev / Next, two big halves) ---
        bottom = tk.Frame(self.root, bg="#1a1a1a")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        self.prev_btn = tk.Button(
            bottom, text="←  Previous", command=lambda: self.go(-1),
            bg="#2a2a2a", fg="#fff", activebackground="#3a3a3a",
            activeforeground="#fff",
            font=("Segoe UI", 14, "bold"), relief="flat", pady=16)
        self.prev_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.next_btn = tk.Button(
            bottom, text="Next  →", command=lambda: self.go(+1),
            bg="#2a4a2a", fg="#fff", activebackground="#3a6a3a",
            activeforeground="#fff",
            font=("Segoe UI", 14, "bold"), relief="flat", pady=16)
        self.next_btn.grid(row=0, column=1, sticky="ew", padx=(8, 0))

    # ---- rendering ------------------------------------------------------- #

    def render(self):
        # Persist state before re-render: in-memory feedback/choice from previous
        # item are flushed by go() before we get here, so this is safe.
        code = self.codes[self.idx]
        row = self.code_to_row[code]

        self.name_lbl.config(text=row["name_de"])
        n_total = len(self.codes)
        n_done = sum(1 for c in self.codes if c in self.state["reviews"])
        pct = (n_done / n_total) * 100
        round_tag = ""
        if self.round_mode:
            # number of rounds = max history length + 1 (for the current one)
            history = self.state.get("review_history", {})
            max_rounds = max((len(v) for v in history.values()), default=0)
            round_tag = f"Round {max_rounds + 1}   ·   "
        self.meta_lbl.config(
            text=f"{round_tag}{code}.png   ·   {row['name_en']}   ·   "
                 f"item {self.idx + 1} of {n_total}   ·   "
                 f"{n_done} reviewed ({pct:.1f}%)")

        # round-N history hint: surface the previous round's feedback so
        # the reviewer immediately sees what was supposed to be fixed.
        hist = self.state.get("review_history", {}).get(code, [])
        if hist:
            last = hist[-1]
            prev_fb = (last.get("feedback") or "").strip()
            prev_choice = last.get("bg_choice", "")
            round_n = len(hist) + 1
            tag = f"Round {round_n}"
            if prev_fb:
                self.history_lbl.config(
                    text=f"{tag} — vorher: \"{prev_fb}\"")
            elif prev_choice == "both_bad":
                self.history_lbl.config(
                    text=f"{tag} — vorher: both bad (kein Freitext)")
            else:
                self.history_lbl.config(text=f"{tag}")
        else:
            self.history_lbl.config(text="")

        # prompt
        prompt = load_prompt(code)
        self.prompt_box.config(state="normal")
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", prompt)
        self.prompt_box.config(state="disabled")

        # images — heavy work, do it once per code
        self._photo_refs.clear()
        raw_path = ICONS_RAW / f"{code}.png"
        alpha_path = ICONS_ALPHA / f"{code}.png"

        try:
            raw_img = Image.open(raw_path)
        except FileNotFoundError:
            raw_img = self._missing_image()

        # PIL flood (cached — slow first time)
        if code in self._pil_cache:
            pil_alpha = self._pil_cache[code]
        else:
            try:
                pil_alpha = pil_flood_alpha(raw_img)
                self._pil_cache[code] = pil_alpha
            except Exception:
                pil_alpha = raw_img.convert("RGBA")

        # massive alpha (already on disk)
        try:
            massive_img = Image.open(alpha_path)
        except FileNotFoundError:
            massive_img = self._missing_image().convert("RGBA")

        size = self._tile_size()
        self._last_tile = size
        pil_canvas = composite_on_checker(pil_alpha, size)
        massive_canvas = composite_on_checker(massive_img, size)
        raw_canvas = fit_white(raw_img, size)

        for label, im in [(self.img_pil, pil_canvas),
                          (self.img_massive, massive_canvas),
                          (self.img_raw, raw_canvas)]:
            tkim = ImageTk.PhotoImage(im)
            label.config(image=tkim)
            self._photo_refs.append(tkim)

        # Round-1 thumbnails: only when icons_round1_*/<code>.png exist (i.e.
        # tools/stash_round1.py was run). Each thumbnail is ~150 px and gets
        # the same treatment as the main panels (PIL flood + checker, BiRefNet
        # + checker, raw on white).
        self._render_round1_thumbs(code)

        # restore review state for this item
        review = self.state["reviews"].get(code, {})
        self.choice.set(review.get("bg_choice", "massive"))
        self.feedback_box.delete("1.0", "end")
        self.feedback_box.insert("1.0", review.get("feedback", ""))
        self._refresh_both_bad()

    def _google_current(self):
        code = self.codes[self.idx]
        row = self.code_to_row[code]
        # Use the German name — the BLS dataset is German and image search
        # there returns the *actual* product the user is reviewing.
        q = urllib.parse.quote_plus(row["name_de"])
        webbrowser.open(f"https://www.google.com/search?tbm=isch&q={q}", new=2)

    def _on_resize(self, _event=None):
        # Debounce: re-render 120 ms after the last resize event, only if the
        # tile size has actually changed (avoid pointless work on cosmetic
        # configures).
        if self._resize_after is not None:
            self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(120, self._maybe_rerender)

    def _maybe_rerender(self):
        self._resize_after = None
        size = self._tile_size()
        if size != self._last_tile:
            self.render()

    def _render_round1_thumbs(self, code: str) -> None:
        thumb_size = 150  # px — small enough to read alongside the big panels
        r1_raw_path = ICONS_R1_RAW / f"{code}.png"
        r1_alpha_path = ICONS_R1_ALPHA / f"{code}.png"
        if not r1_raw_path.exists() and not r1_alpha_path.exists():
            for lbl in (self.thumb_pil, self.thumb_massive, self.thumb_raw):
                lbl.config(image="", text="")
            return

        try:
            r1_raw = Image.open(r1_raw_path)
        except FileNotFoundError:
            r1_raw = self._missing_image()

        if code in self._pil_cache_r1:
            r1_pil = self._pil_cache_r1[code]
        else:
            try:
                r1_pil = pil_flood_alpha(r1_raw)
                self._pil_cache_r1[code] = r1_pil
            except Exception:
                r1_pil = r1_raw.convert("RGBA")

        try:
            r1_alpha = Image.open(r1_alpha_path)
        except FileNotFoundError:
            r1_alpha = self._missing_image().convert("RGBA")

        thumbs = [
            (self.thumb_pil, composite_on_checker(r1_pil, thumb_size)),
            (self.thumb_massive, composite_on_checker(r1_alpha, thumb_size)),
            (self.thumb_raw, fit_white(r1_raw, thumb_size)),
        ]
        for lbl, im in thumbs:
            tkim = ImageTk.PhotoImage(im)
            lbl.config(image=tkim, text="")
            self._photo_refs.append(tkim)

    def _tile_size(self) -> int:
        # Read the *actual* allocated cell of the middle image label after
        # Tk has done a layout pass. That way the tile fills its grid cell
        # exactly, regardless of how many rows of controls live below it.
        self.panels.update_idletasks()
        w = self.img_massive.winfo_width()
        h = self.img_massive.winfo_height()
        if w < 50 or h < 50:
            # First call before the window has been mapped — fall back to a
            # geometry estimate. Subsequent calls will use the real size.
            self.root.update_idletasks()
            w = max(self.panels.winfo_width() // 4 - 16, 280)
            h = max(self.panels.winfo_height() - 260, 280)
        return max(120, min(w, h) - 4)

    def _missing_image(self) -> Image.Image:
        img = Image.new("RGB", (1024, 1024), "#222")
        d = ImageDraw.Draw(img)
        d.text((30, 30), "(missing)", fill="#888")
        return img

    # ---- state mutation -------------------------------------------------- #

    def _flush_current(self):
        """Persist whatever the user has set for the current item to state."""
        code = self.codes[self.idx]
        feedback = self.feedback_box.get("1.0", "end").strip()
        choice = self.choice.get()
        # Anything was actually engaged?
        prev = self.state["reviews"].get(code, {})
        # Treat a default-untouched item (massive selected, no feedback) as
        # *reviewed* anyway, because the act of nav-ing past it is an
        # implicit "I looked at it, looked fine".
        self.state["reviews"][code] = {
            "bg_choice": choice,
            "feedback": feedback,
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # Drop spurious updates if nothing actually changed (avoid touching
        # reviewed_at on a pure navigation).
        if prev and prev.get("bg_choice") == choice and prev.get("feedback") == feedback:
            self.state["reviews"][code] = prev
        self._persist_position()
        save_state(self.state)

    def _persist_position(self) -> None:
        """Stash the current position both as a stable code and as a legacy
        global-list index, so reopening the tool always lands on the right
        item regardless of round-mode filtering."""
        code = self.codes[self.idx]
        self.state["current_code"] = code
        try:
            self.state["current_index"] = self._global_codes.index(code)
        except ValueError:
            self.state["current_index"] = 0

    def go(self, delta: int):
        self._flush_current()
        new = self.idx + delta
        if new < 0 or new >= len(self.codes):
            return
        self.idx = new
        self._persist_position()
        save_state(self.state)
        self.render()

    def jump_to(self, query: str):
        q = query.strip().upper()
        if not q:
            return
        # In round-mode, jumping is restricted to the round-2 set —
        # otherwise the navigation list and the position drift apart.
        if q in self.codes:
            self._flush_current()
            self.idx = self.codes.index(q)
            self._persist_position()
            save_state(self.state)
            self.render()
            return
        # try prefix match within current navigation set
        for i, c in enumerate(self.codes):
            if c.startswith(q):
                self._flush_current()
                self.idx = i
                self._persist_position()
                save_state(self.state)
                self.render()
                return
        # try substring match against names
        ql = query.strip().lower()
        for i, row in enumerate(self.items):
            if ql in row["name_de"].lower() or ql in row["name_en"].lower():
                self._flush_current()
                self.idx = i
                self._persist_position()
                save_state(self.state)
                self.render()
                return

    def _save_choice(self):
        # radio button change → persist immediately so even closing the window
        # mid-item doesn't lose the toggle.
        self._flush_current()
        self._refresh_both_bad()

    def _toggle_both_bad(self):
        if self.choice.get() == "both_bad":
            self.choice.set("massive")
        else:
            self.choice.set("both_bad")
        self._save_choice()

    def _refresh_both_bad(self):
        if self.choice.get() == "both_bad":
            self.both_bad_btn.config(
                text="✓  marked: both bad — click to undo",
                bg="#883333")
        else:
            self.both_bad_btn.config(
                text="✕  both bad — regenerate",
                bg="#5a2222")


def main():
    if not ITEMS_CSV.exists():
        sys.exit(f"items.csv not found at {ITEMS_CSV}")
    root = tk.Tk()
    try:
        # lift on Windows so the window opens to front
        root.attributes("-topmost", True)
        root.after(100, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass
    app = ReviewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
