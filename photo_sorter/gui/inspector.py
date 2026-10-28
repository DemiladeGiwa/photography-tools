"""Right-hand panel: details, scores, explanations and decision controls for the selection.

The widgets are built once and updated in place, and the layout keeps the same
height from photo to photo (text areas only ever grow). On Windows, rebuilding
the panel cost ~200 ms per arrow key and any height change ~50 ms; changing
text in place costs ~2 ms.
"""

from __future__ import annotations

import math
import re
import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from ..core.constants import COLOR_LABELS
from ..core.library import FLAG_PICK, FLAG_REJECT, Photo
from . import theme as T
from .theme import px

KIND_NAMES = {"burst": "Burst", "similar": "Similar shots", "duplicate": "Duplicates"}
MAX_REASONS = 6


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class _Bar(tk.Canvas):
    """A thin score bar that can be updated."""

    def __init__(self, parent, width: int = 110, height: int = 6):
        super().__init__(parent, width=px(width), height=px(height), bg=T.SURF, highlightthickness=0, bd=0)
        self._bar_w, self._bar_h = px(width), px(height)
        self._fill = self.create_rectangle(0, 0, 0, self._bar_h, fill=T.SURF, width=0)

    def set(self, value: float, color: str) -> None:
        self.coords(self._fill, 0, 0, self._bar_w * max(0.0, min(1.0, value / 100.0)), self._bar_h)
        self.itemconfigure(self._fill, fill=color)


class _Section(tk.Frame):
    """A titled block of the panel (title + body) that can be shown or hidden as a whole."""

    def __init__(self, parent, title: str):
        super().__init__(parent, bg=T.PANEL)
        self.title = tk.Label(self, text=title.upper(), bg=T.PANEL, fg=T.FAINT, font=T.font("tiny"), anchor="w")
        self.title.pack(fill="x", padx=px(14), pady=(px(14), px(4)))
        self.body = tk.Frame(self, bg=T.PANEL)
        self.body.pack(fill="x", padx=px(14))


def _lines_needed(text: str, font_name: str, wrap: int) -> int:
    """Roughly how many lines ``text`` takes when wrapped at ``wrap`` px (errs on the high side)."""
    f = T.font(font_name)
    return sum(max(1, math.ceil(f.measure(line) * 1.08 / max(1, wrap))) for line in (text.split("\n") or [""]))


def _grow_only(label: tk.Label, text: str, font_name: str, wrap: int, minimum: int = 1) -> None:
    """Set a wrapped label's text, raising (never lowering) its height in lines, so the panel doesn't
    change height - and need a full ~50 ms relayout - every time a different photo is shown."""
    lines = max(minimum, _lines_needed(text, font_name, wrap))
    if lines > int(label.cget("height") or 0):
        label.configure(height=lines)
    _set(label, text=text)


class _Row(tk.Frame):
    """``label   value`` line in the Info / Summary sections."""

    WRAP = 200

    def __init__(self, parent, label: str):
        super().__init__(parent, bg=T.PANEL)
        tk.Label(self, text=label, bg=T.PANEL, fg=T.MUTE, font=T.font("small"), anchor="nw", width=10).pack(
            side="left", fill="y")
        self.value = tk.Label(self, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("small"), anchor="nw",
                              justify="left", wraplength=px(self.WRAP), height=1)
        self.value.pack(side="left", fill="x", expand=True)

    def set(self, text: str, color: str = T.TEXT) -> None:
        _grow_only(self.value, text, "small", px(self.WRAP))
        _set(self.value, fg=color)


def _show_in_order(widgets: List[tk.Widget], visible: List[bool], **pack_kw) -> None:
    """Pack the visible widgets in their fixed order. Untouched when the visible set hasn't changed."""
    wanted = [w for w, v in zip(widgets, visible) if v]
    if [w for w in widgets if w.winfo_manager()] == wanted:
        return
    for w in widgets:
        w.pack_forget()
    for w in wanted:
        w.pack(**pack_kw)


def _set(widget: tk.Widget, **kw) -> None:
    """configure() only what changed (each change can trigger a relayout)."""
    changed = {k: v for k, v in kw.items() if str(widget.cget(k)) != str(v)}
    if changed:
        widget.configure(**changed)


class Inspector(tk.Frame):
    WIDTH = 310
    PREVIEW_H = 200

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.PANEL, width=px(self.WIDTH))
        self.pack_propagate(False)
        self.app = app
        self.scroll = T.ScrollFrame(self, bg=T.PANEL)
        self.scroll.pack(fill="both", expand=True)
        self.inner = self.scroll.inner
        self.photo: Optional[Photo] = None
        self.selection: List[Photo] = []
        self._img = None
        self._last_show = 0.0
        self._show_job = None
        self._layout: tuple = ()
        self._build()
        self.app.images.add_listener(self._thumbs_ready)
        self._show_now(None, [])

    # ------------------------------------------------------------ building (once)
    def _build(self) -> None:
        inner = self.inner
        self.w_empty = tk.Label(inner, text="Select a photo to see\nits scores and details.", bg=T.PANEL, fg=T.MUTE,
                                font=T.font("body"), justify="center")

        # --- one photo: preview + name
        self.w_head = tk.Frame(inner, bg=T.PANEL)
        self.preview = tk.Canvas(self.w_head, height=px(self.PREVIEW_H), bg=T.GRID_BG, highlightthickness=0)
        self.preview.pack(fill="x", padx=px(14), pady=(px(14), 0))
        self.preview.bind("<Configure>", lambda e: self._draw_preview())
        self.preview.bind("<Double-Button-1>", lambda e: self.photo and self.app.open_viewer(self.photo))
        self.name = tk.Label(self.w_head, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("h3"), anchor="nw",
                             justify="left", wraplength=px(280), height=1)
        self.name.pack(fill="x", padx=px(14), pady=(px(8), 0))
        self.sub = tk.Label(self.w_head, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"), anchor="w")
        self.sub.pack(fill="x", padx=px(14))

        # --- several photos: title
        self.w_multi_head = tk.Frame(inner, bg=T.PANEL)
        self.multi_title = tk.Label(self.w_multi_head, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("h2"), anchor="w")
        self.multi_title.pack(fill="x", padx=px(14), pady=(px(16), 0))
        tk.Label(self.w_multi_head, text="Decisions apply to all selected photos.", bg=T.PANEL, fg=T.MUTE,
                 font=T.font("small"), anchor="w").pack(fill="x", padx=px(14))

        self._build_decision_bar(inner)

        # --- problem / not analysed
        self.w_problem = _Section(inner, "Problem")
        self.problem = tk.Label(self.w_problem.body, text="", bg=T.PANEL, fg=T.REJECT, font=T.font("small"),
                                justify="left", wraplength=px(270), anchor="w")
        self.problem.pack(fill="x")
        self.w_pending = _Section(inner, "Scores")
        tk.Label(self.w_pending.body, text="Not analysed yet - click Analyse in the top bar.", bg=T.PANEL,
                 fg=T.MUTE, font=T.font("small"), anchor="w").pack(fill="x")

        # --- scores
        self.w_scores = _Section(inner, "Scores")
        top = tk.Frame(self.w_scores.body, bg=T.PANEL)
        top.pack(fill="x")
        self.q_big = tk.Label(top, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("big"))
        self.q_big.pack(side="left")
        tk.Label(top, text=" overall\n quality", bg=T.PANEL, fg=T.MUTE, font=T.font("small"),
                 justify="left").pack(side="left")
        self.best_badge = tk.Label(top, text="★ Best of group", bg=T.ACC, fg=T.ACC_TEXT, font=T.font("tiny"),
                                   padx=px(6), pady=px(2))
        self.score_rows = {}
        for name in ("Sharpness", "Exposure"):
            r = tk.Frame(self.w_scores.body, bg=T.PANEL)
            r.pack(fill="x", pady=(px(6), 0))
            tk.Label(r, text=name, bg=T.PANEL, fg=T.MUTE, font=T.font("small"), width=10, anchor="w").pack(side="left")
            b = _Bar(r)
            b.pack(side="left", padx=(0, px(8)))
            v = tk.Label(r, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("small"))
            v.pack(side="left")
            self.score_rows[name] = (b, v)

        self.w_why = _Section(inner, "Why")
        # One label for all reasons: re-wrapping and re-packing separate labels cost ~50 ms per photo.
        self.reasons = tk.Label(self.w_why.body, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("small"),
                                justify="left", anchor="nw", wraplength=px(270), height=3)
        self.reasons.pack(fill="x", pady=1)

        self.w_hist = _Section(inner, "Histogram")
        self.hist = tk.Canvas(self.w_hist.body, width=px(280), height=px(90), bg=T.GRID_BG, highlightthickness=0)
        self.hist.pack(fill="x")
        self.hist_note = tk.Label(self.w_hist.body, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("tiny"), anchor="w")
        self.hist_note.pack(fill="x", pady=(px(3), 0))

        # --- group
        self.w_group = _Section(inner, "Group")
        self.group_text = tk.Label(self.w_group.body, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("small"), anchor="w")
        self.group_text.pack(fill="x")
        bb = tk.Frame(self.w_group.body, bg=T.PANEL)
        bb.pack(fill="x", pady=(px(6), 0))
        view_btn = ttk.Button(bb, text="View group", style="Small.TButton",
                              command=lambda: self.photo and self.photo.group_id and self.app.show_group(self.photo.group_id))
        view_btn.pack(side="left")
        self.make_best_btn = ttk.Button(bb, text="Make best", style="Small.TButton",
                                        command=lambda: self.photo and self.app.make_best(self.photo))
        self.make_best_btn.pack(side="left", padx=px(4))
        compare_btn = ttk.Button(bb, text="Compare", style="Small.TButton", command=self._compare_group)
        compare_btn.pack(side="left", padx=px(4))
        self.group_btns = (view_btn, self.make_best_btn, compare_btn)

        # --- info
        self.w_info = _Section(inner, "Info")
        self.info_rows: Dict[str, _Row] = {k: _Row(self.w_info.body, k)
                                           for k in ("Captured", "Camera", "Lens", "Settings", "Size", "Files")}
        self.w_files = tk.Frame(inner, bg=T.PANEL)
        ttk.Button(self.w_files, text="Open", style="Small.TButton",
                   command=lambda: self.photo and self.app.open_external(self.photo)).pack(side="left")
        ttk.Button(self.w_files, text="Show in folder", style="Small.TButton",
                   command=lambda: self.photo and self.app.reveal(self.photo)).pack(side="left", padx=px(4))

        # --- several photos: summary
        self.w_summary = _Section(inner, "Summary")
        self.sum_rows = {k: _Row(self.w_summary.body, k) for k in ("Picks", "Rejects", "Avg quality", "Highest")}
        self.w_multi_btns = tk.Frame(inner, bg=T.PANEL)
        ttk.Button(self.w_multi_btns, text="Compare (C)", style="Small.TButton",
                   command=lambda: self.app.open_compare(self.selection[:4])).pack(side="left")
        ttk.Button(self.w_multi_btns, text="Keep best, reject rest", style="Small.TButton",
                   command=lambda: self.app.keep_best_of(self.selection)).pack(side="left", padx=px(4))

    def _build_decision_bar(self, inner) -> None:
        self.w_decide = tk.Frame(inner, bg=T.PANEL)
        f = tk.Frame(self.w_decide, bg=T.PANEL)
        f.pack(fill="x", padx=px(14), pady=(px(10), 0))
        self.flag_btns = {}
        for text, flag, key in (("✓ Pick", FLAG_PICK, "P"), ("✕ Reject", FLAG_REJECT, "X")):
            b = tk.Label(f, text=text, bg=T.SURF, fg=T.TEXT, font=T.font("bold"), padx=px(10), pady=px(6),
                         cursor="hand2")
            b.pack(side="left", padx=(0, px(4)), fill="x", expand=True)
            b.bind("<Button-1>", lambda e, fl=flag: self.app.decide_flag(None if self._flag == fl else fl))
            T.tip(b, f"Shortcut: {key}")
            self.flag_btns[flag] = b
        b = tk.Label(f, text="Clear", bg=T.SURF, fg=T.MUTE, font=T.font("small"), padx=px(8), pady=px(6),
                     cursor="hand2")
        b.pack(side="left")
        b.bind("<Button-1>", lambda e: self.app.decide_flag(None))
        T.tip(b, "Shortcut: U")
        self._flag: Optional[str] = None

        r = tk.Frame(self.w_decide, bg=T.PANEL)
        r.pack(fill="x", padx=px(14), pady=(px(8), 0))
        self.stars = T.StarRating(r, lambda n: self.app.decide_rating(n))
        self.stars.pack(side="left")
        T.tip(self.stars, "Shortcut: 1-5, 0 clears")
        lab = tk.Frame(r, bg=T.PANEL)
        lab.pack(side="right")
        self.label_dots = {}
        for i, name in enumerate(COLOR_LABELS[:4]):
            d = tk.Canvas(lab, width=px(18), height=px(18), bg=T.PANEL, highlightthickness=0, cursor="hand2")
            col = T.LABEL_COLORS[name]
            oval = d.create_oval(px(3), px(3), px(15), px(15), fill=col, outline=col, width=1)
            d.pack(side="left", padx=1)
            d.bind("<Button-1>", lambda e, n=name: self.app.decide_label(n))
            T.tip(d, f"{name} label (key {6 + i})")
            self.label_dots[name] = (d, oval, col)

    # ------------------------------------------------------------ updating
    def _thumbs_ready(self, keys) -> None:
        if self.photo is not None and self.photo.key in keys and len(self.selection) <= 1:
            self._draw_preview()

    def show(self, photo: Optional[Photo], selection: List[Photo]) -> None:
        """Show details for ``photo``. Calls in quick succession (an arrow key held down) are merged."""
        now = time.monotonic()
        rapid = now - self._last_show < 0.08
        self._last_show = now
        if self._show_job is not None:
            self.after_cancel(self._show_job)
            self._show_job = None
        if rapid:
            self._show_job = self.after(60, lambda: self._show_now(photo, selection))
        else:
            self._show_now(photo, selection)

    def _show_now(self, photo: Optional[Photo], selection: List[Photo]) -> None:
        self._show_job = None
        self.photo, self.selection = photo, list(selection)
        if photo is None:
            self._arrange(("empty",))
            return
        if len(selection) > 1:
            self._show_multi(selection)
            return
        self._show_one(photo)

    def _arrange(self, parts: tuple) -> None:
        """Pack exactly these blocks, in order. Skipped when nothing changed, so updates don't relayout."""
        if parts == self._layout:
            return
        self._layout = parts
        blocks = {
            "empty": (self.w_empty, {"pady": px(60)}),
            "head": (self.w_head, {"fill": "x"}),
            "multi_head": (self.w_multi_head, {"fill": "x"}),
            "decide": (self.w_decide, {"fill": "x"}),
            "problem": (self.w_problem, {"fill": "x"}),
            "pending": (self.w_pending, {"fill": "x"}),
            "scores": (self.w_scores, {"fill": "x"}),
            "why": (self.w_why, {"fill": "x"}),
            "hist": (self.w_hist, {"fill": "x"}),
            "group": (self.w_group, {"fill": "x"}),
            "info": (self.w_info, {"fill": "x"}),
            "files": (self.w_files, {"fill": "x", "padx": px(14), "pady": (px(12), px(16))}),
            "summary": (self.w_summary, {"fill": "x"}),
            "multi_btns": (self.w_multi_btns, {"fill": "x", "padx": px(14), "pady": px(12)}),
        }
        for w, _kw in blocks.values():
            w.pack_forget()
        for name in parts:
            w, kw = blocks[name]
            w.pack(**kw)
        self.scroll.canvas.yview_moveto(0)

    def _update_decision_bar(self, photos: List[Photo]) -> None:
        flags = {p.flag for p in photos}
        self._flag = flags.pop() if len(flags) == 1 else "mixed"
        for flag, b in self.flag_btns.items():
            on = self._flag == flag
            _set(b, bg=(T.PICK if flag == FLAG_PICK else T.REJECT) if on else T.SURF, fg="#101010" if on else T.TEXT)
        ratings = {p.rating for p in photos}
        self.stars.set(ratings.pop() if len(ratings) == 1 else 0)
        labels = {p.label for p in photos}
        cur = labels.pop() if len(labels) == 1 else None
        for name, (d, oval, col) in self.label_dots.items():
            d.itemconfigure(oval, outline=T.TEXT if cur == name else col, width=px(2) if cur == name else 1)

    def _show_one(self, p: Photo) -> None:
        parts = ["head", "decide"]
        _grow_only(self.name, p.name, "h3", px(280))
        sub = [p.type_label, _human_size(p.size_bytes)]
        if p.rel_dir:
            sub.append(p.rel_dir)
        _set(self.sub, text="  ·  ".join(sub))
        self._update_decision_bar([p])
        self._draw_preview()

        if p.error:
            parts.append("problem")
            _set(self.problem, text=f"This file couldn't be read:\n{p.error}")
        elif p.quality is None:
            parts.append("pending")
        else:
            parts += ["scores", "why"]
            q = p.quality
            _set(self.q_big, text=f"{q.quality_score:.0f}", fg=T.score_color(q.quality_score))
            if p.is_best and not self.best_badge.winfo_manager():
                self.best_badge.pack(side="right")
            elif not p.is_best:
                self.best_badge.pack_forget()
            for name, val, lbl in (("Sharpness", q.sharpness_score, q.sharpness_label),
                                   ("Exposure", q.exposure_score, q.exposure_label)):
                bar, v = self.score_rows[name]
                bar.set(val, T.score_color(val))
                _set(v, text=f"{val:.0f}  {lbl}")
            reasons = []
            if p.blur:
                reasons.extend(p.blur.reasons[:2])
            reasons.extend(r for r in q.reasons if r not in reasons)
            reasons = [re.sub(r"\s*\[[^\]]*\]", "", r) for r in reasons[:MAX_REASONS]]
            _grow_only(self.reasons, "\n".join("•  " + r for r in reasons), "small", px(270), minimum=3)
            exp = p.data.get("exposure") or {}
            hist = exp.get("histogram_rgb")
            if hist:
                parts.append("hist")
                self._histogram(hist, exp)

        # Always shown (greyed out when the photo isn't in a group) so the panel keeps its height.
        g = self.app.lib.groups_by_id.get(p.group_id) if (p.group_id and self.app.lib) else None
        parts.append("group")
        if g:
            kind = KIND_NAMES.get(g.kind, g.kind)
            _set(self.group_text, text=f"{kind} #{g.id} · {len(g.members)} photos"
                                       + ("  ·  this is the best" if p.is_best else ""), fg=T.TEXT)
        else:
            _set(self.group_text, text="Not part of a burst or group", fg=T.FAINT)
        for b in self.group_btns:
            want = "normal" if g and not (b is self.make_best_btn and p.is_best) else "disabled"
            if str(b.cget("state")) != want:
                b.configure(state=want)

        parts += ["info", "files"]
        self._update_info(p)
        self._arrange(tuple(parts))

    def _update_info(self, p: Photo) -> None:
        md = p.meta
        vals: Dict[str, str] = {}
        if p.capture_time:
            vals["Captured"] = p.capture_time.strftime("%a %d %b %Y, %H:%M:%S") + (
                "" if p.time_from_exif else "  (file date)")
        cam = md.model or ""
        if md.make and not cam.lower().startswith(md.make.split()[0].lower()):
            cam = f"{md.make} {cam}".strip()
        if cam:
            vals["Camera"] = cam
        if md.lens:
            vals["Lens"] = md.lens
        settings = md.format_exif_summary()
        if settings != "No EXIF data":
            vals["Settings"] = settings.replace(md.model or "~~", "").strip(" •")
        if md.width and md.height:
            vals["Size"] = f"{md.width} × {md.height}  ({md.width * md.height / 1e6:.1f} MP)"
        vals["Files"] = ", ".join(f.name for f in p.files)
        for key, row in self.info_rows.items():  # every row stays, so the panel keeps its height
            if key in vals:
                row.set(vals[key])
            else:
                row.set("–", T.FAINT)
        _show_in_order(list(self.info_rows.values()), [True] * len(self.info_rows), fill="x", pady=1)

    def _draw_preview(self) -> None:
        if self.photo is None or len(self.selection) > 1:
            return
        c = self.preview
        try:
            w = c.winfo_width()
        except tk.TclError:
            return
        if w < 10:
            return
        h = px(self.PREVIEW_H)  # fixed, so portrait and landscape photos don't resize the panel
        img = self.app.images.thumb(self.photo, min(px(340), w), h)
        c.delete("all")
        if img is not None:
            c.create_image(w // 2, h // 2, image=img)
            self._img = img

    def _histogram(self, hist: dict, exp: dict) -> None:
        c = self.hist
        c.delete("all")
        w, h = px(280), px(90)
        vals = [max(hist.get(ch, [0])) for ch in ("r", "g", "b")]
        peak = max(max(vals), 1e-6)
        colors = {"r": "#e5534b", "g": "#3fb96b", "b": "#4a8cff"}
        for ch in ("r", "g", "b"):
            arr = hist.get(ch) or []
            if len(arr) < 2:
                continue
            n = len(arr)
            pts = []
            for i, v in enumerate(arr):
                pts.extend((i * w / (n - 1), h - min(1.0, (v / peak) ** 0.6) * (h - 4)))
            c.create_line(*pts, fill=colors[ch], width=1, smooth=True)
        hl = exp.get("highlight_clip_frac", 0) * 100
        sh = exp.get("shadow_clip_frac", 0) * 100
        _set(self.hist_note, text=f"Clipped highlights {hl:.1f}%   ·   Crushed shadows {sh:.1f}%",
             fg=T.REJECT if hl > 1 or sh > 3 else T.MUTE)

    def _compare_group(self) -> None:
        p = self.photo
        if p is not None and p.group_id and self.app.lib:
            self.app.open_compare(self.app.lib.group_members(p.group_id)[:4])

    def _show_multi(self, photos: List[Photo]) -> None:
        _set(self.multi_title, text=f"{len(photos)} photos selected")
        self._update_decision_bar(photos)
        picks = sum(1 for p in photos if p.flag == FLAG_PICK)
        rejects = sum(1 for p in photos if p.flag == FLAG_REJECT)
        analysed = [p for p in photos if p.quality]
        self.sum_rows["Picks"].set(str(picks), T.PICK if picks else T.TEXT)
        self.sum_rows["Rejects"].set(str(rejects), T.REJECT if rejects else T.TEXT)
        _show_in_order(list(self.sum_rows.values()), [True, True, bool(analysed), bool(analysed)], fill="x", pady=1)
        if analysed:
            avg = sum(p.quality_score for p in analysed) / len(analysed)
            best = max(analysed, key=lambda p: p.quality_score)
            self.sum_rows["Avg quality"].set(f"{avg:.0f}")
            self.sum_rows["Highest"].set(f"{best.name} ({best.quality_score:.0f})")
        self._arrange(("multi_head", "decide", "summary", "multi_btns"))
