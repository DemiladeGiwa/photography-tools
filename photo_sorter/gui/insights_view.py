"""Insights: what you shot, what you kept, and how sharp it was."""

from __future__ import annotations

import tkinter as tk
from typing import List

from ..core import stats as st
from . import theme as T
from .theme import px


class BarChart(tk.Canvas):
    """Bars for photos (muted) with the picks portion in accent. Grows in when shown."""

    def __init__(self, parent, horizontal: bool = True, height: int = 190):
        super().__init__(parent, bg=T.SURF, highlightthickness=0, height=px(height))
        self.horizontal = horizontal
        self.buckets: List[st.Bucket] = []
        self._t = 1.0
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, buckets: List[st.Bucket], animate: bool = True) -> None:
        self.buckets = buckets
        if animate:
            T.animate(self, 520, self._grow, key=("chart", str(self)))
        else:
            self._grow(1.0)

    def _grow(self, t: float) -> None:
        self._t = t
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        data = [b for b in self.buckets]
        if not data or max((b.count for b in data), default=0) == 0:
            self.create_text(W // 2, H // 2, text="No data", fill=T.FAINT, font=T.font("small"))
            return
        peak = max(b.count for b in data)
        f_small = T.font("tiny")
        if self.horizontal:
            label_w = min(px(170), max(f_small.measure(b.label) for b in data) + px(10))
            n = len(data)
            row = min(px(26), (H - px(4)) / n)
            bar_h = max(px(6), row * 0.62)
            for i, b in enumerate(data):
                y = px(2) + i * row + (row - bar_h) / 2
                self.create_text(label_w - px(6), y + bar_h / 2, text=_clip(b.label, 26), fill=T.MUTE, font=f_small,
                                 anchor="e")
                full = (W - label_w - px(70)) * b.count / peak * self._t
                self.create_rectangle(label_w, y, label_w + full, y + bar_h, fill=T.SURF2, width=0)
                pk = (W - label_w - px(70)) * b.picks / peak * self._t
                if pk > 0:
                    self.create_rectangle(label_w, y, label_w + pk, y + bar_h, fill=T.ACC, width=0)
                txt = f"{b.count}" + (f"  ·  {b.picks} kept" if b.picks else "")
                self.create_text(label_w + full + px(6), y + bar_h / 2, text=txt, fill=T.TEXT, font=f_small, anchor="w")
        else:
            n = len(data)
            base = H - px(20)
            col = (W - px(8)) / n
            bw = max(px(3), min(px(64), col * 0.7))
            step_labels = max(1, int(n / max(1, (W // px(60)))))
            for i, b in enumerate(data):
                x = px(4) + i * col + (col - bw) / 2
                h = (base - px(6)) * b.count / peak * self._t
                self.create_rectangle(x, base - h, x + bw, base, fill=T.SURF2, width=0)
                hp = (base - px(6)) * b.picks / peak * self._t
                if hp > 0:
                    self.create_rectangle(x, base - hp, x + bw, base, fill=T.ACC, width=0)
                if i % step_labels == 0:
                    self.create_text(x + bw / 2, base + px(9), text=b.label, fill=T.FAINT, font=f_small)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class InsightsView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.sf = T.ScrollFrame(self)
        self.sf.pack(fill="both", expand=True)
        root = self.sf.inner
        head = tk.Frame(root, bg=T.BG)
        head.pack(fill="x", padx=px(24), pady=(px(18), px(6)))
        tk.Label(head, text="Insights", bg=T.BG, fg=T.TEXT, font=T.font("h1"), anchor="w").pack(fill="x")
        self.sub = tk.Label(head, text="", bg=T.BG, fg=T.MUTE, anchor="w")
        self.sub.pack(fill="x")

        tiles = tk.Frame(root, bg=T.BG)
        tiles.pack(fill="x", padx=px(24), pady=(px(6), px(12)))
        self.tiles = {}
        for i, (key, label, color) in enumerate((("total", "photos", T.TEXT), ("picks", "picks", T.PICK),
                                                ("keeper", "keeper rate", T.ACC), ("rejects", "rejects", T.REJECT),
                                                ("blurry", "blurry", T.WARN), ("quality", "avg quality", T.INFO))):
            f = tk.Frame(tiles, bg=T.SURF, padx=px(16), pady=px(12))
            f.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(10), 0))
            tiles.columnconfigure(i, weight=1, uniform="t")
            v = tk.Label(f, text="–", bg=T.SURF, fg=color, font=T.font("big"))
            v.pack(anchor="w")
            tk.Label(f, text=label, bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(anchor="w")
            self.tiles[key] = v

        grid = tk.Frame(root, bg=T.BG)
        grid.pack(fill="both", expand=True, padx=px(24), pady=(0, px(24)))
        grid.columnconfigure(0, weight=1, uniform="g")
        grid.columnconfigure(1, weight=1, uniform="g")
        self.charts = {}
        spec = [
            ("timeline", "When you shot", "Photos over time - amber is what you kept.", False, 0, 0, 2),
            ("quality", "Quality spread", "How the analysis scored the shoot (0-100).", False, 1, 0, 1),
            ("sharp", "Sharpness spread", "Left = soft, right = tack sharp.", False, 1, 1, 1),
            ("cameras", "Cameras", "", True, 2, 0, 1),
            ("lenses", "Lenses", "", True, 2, 1, 1),
            ("focal", "Focal length", "", True, 3, 0, 1),
            ("aperture", "Aperture", "", True, 3, 1, 1),
            ("iso", "ISO", "", True, 4, 0, 1),
            ("shutter", "Shutter speed", "", True, 4, 1, 1),
        ]
        for key, title, sub, horiz, r, c, span in spec:
            card = T.Card(grid, title, sub)
            card.grid(row=r, column=c, columnspan=span, sticky="nsew", padx=(0 if c == 0 else px(6), 0 if c + span == 2 else px(6)),
                      pady=(0, px(12)))
            ch = BarChart(card.body, horizontal=horiz, height=150 if not horiz else 190)
            ch.pack(fill="both", expand=True)
            self.charts[key] = ch

    def on_show(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        s = st.compute(lib.photos, len(lib.groups))
        span = ""
        if s.first and s.last:
            if s.first.date() == s.last.date():
                span = f"{s.first:%a %d %b %Y}, {s.first:%H:%M}–{s.last:%H:%M}"
            else:
                span = f"{s.first:%d %b %Y} – {s.last:%d %b %Y}"
        self.sub.configure(text=f"{lib.root.name}  ·  {span}  ·  {s.groups} bursts/groups  ·  "
                                f"{s.analysed} of {s.total} analysed")
        T.count_up(self.tiles["total"], s.total)
        T.count_up(self.tiles["picks"], s.picks)
        T.count_up(self.tiles["keeper"], s.keeper_rate * 100, "{:.0f}%")
        T.count_up(self.tiles["rejects"], s.rejects)
        T.count_up(self.tiles["blurry"], s.blurry)
        T.count_up(self.tiles["quality"], s.avg_quality or 0)
        self.charts["timeline"].set(s.timeline)
        self.charts["quality"].set(s.quality_hist)
        self.charts["sharp"].set(s.sharpness_hist)
        self.charts["cameras"].set(s.cameras)
        self.charts["lenses"].set(s.lenses)
        self.charts["focal"].set([b for b in s.focal if b.count] or s.focal)
        self.charts["aperture"].set([b for b in s.aperture if b.count] or s.aperture)
        self.charts["iso"].set([b for b in s.iso if b.count] or s.iso)
        self.charts["shutter"].set([b for b in s.shutter if b.count] or s.shutter)
