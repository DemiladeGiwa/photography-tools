"""Start screen shown when no folder is open."""

from __future__ import annotations

import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .. import __version__
from ..core.constants import ALL_EXTS
from . import theme as T
from .theme import px

TIPS = [
    "Press P to pick, X to reject and 1-5 to rate. Auto-advance jumps to the next photo for you.",
    "Space opens the loupe. Press H there for the focus map - green is sharp, red is soft.",
    "Groups finds bursts and near-duplicates and marks the sharpest frame of each.",
    "Auto-Cull can pick the best of every burst and reject the blurry ones in one click.",
    "Right-click any photo and choose 'Find similar photos' to see every shot like it.",
    "Export can make share-ready JPEGs with a watermark, or hand your ratings to Lightroom.",
    "Every move, rename and delete can be undone from History.",
]


def _quick_count(folder: Path, limit: int = 5000) -> int:
    n = 0
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in ALL_EXTS:
                n += 1
                if n >= limit:
                    break
    except OSError:
        return -1
    return n


class WelcomeView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.wrap = tk.Frame(self, bg=T.BG)
        self.wrap.place(relx=0.5, rely=0.46, anchor="center")
        self._tip_i = int(time.time()) % len(TIPS)

        hero = tk.Frame(self.wrap, bg=T.BG)
        hero.pack(fill="x")
        self.logo = tk.Canvas(hero, width=px(64), height=px(64), bg=T.BG, highlightthickness=0)
        self.logo.pack(side="left", padx=(0, px(16)))
        txt = tk.Frame(hero, bg=T.BG)
        txt.pack(side="left")
        tk.Label(txt, text="Photography Tools", bg=T.BG, fg=T.TEXT, font=T.font("big")).pack(anchor="w")
        tk.Label(txt, text="Cull, group, organise and deliver a shoot - fast.", bg=T.BG, fg=T.MUTE,
                 font=T.font("body")).pack(anchor="w")

        actions = tk.Frame(self.wrap, bg=T.BG)
        actions.pack(fill="x", pady=(px(26), px(8)))
        self._action_card(actions, "▦", "Open a folder", "Photos already on your computer", app.open_folder, 0)
        self._action_card(actions, "⤓", "Import from card", "Copy from a memory card, sorted by date",
                          lambda: app.switch_view("import"), 1)

        tk.Label(self.wrap, text="RECENT", bg=T.BG, fg=T.FAINT, font=T.font("tiny"), anchor="w").pack(
            fill="x", pady=(px(18), px(4)))
        self.recent = tk.Frame(self.wrap, bg=T.BG)
        self.recent.pack(fill="x")

        self.tip = tk.Label(self.wrap, text="", bg=T.BG, fg=T.MUTE, font=T.font("small"), anchor="w",
                            justify="left", wraplength=px(560))
        self.tip.pack(fill="x", pady=(px(22), 0))
        tk.Label(self, text=f"v{__version__}  ·  drag a folder onto this window to open it", bg=T.BG, fg=T.FAINT,
                 font=T.font("tiny")).place(relx=0.5, rely=1.0, y=-px(14), anchor="s")
        self._draw_logo(0.0)

    def _draw_logo(self, t: float) -> None:
        c = self.logo
        c.delete("all")
        s = px(64)
        # aperture-style mark: amber ring + rotating blades
        import math

        c.create_oval(px(4), px(4), s - px(4), s - px(4), outline=T.ACC, width=px(4))
        cx = cy = s / 2
        for k in range(6):
            a = math.radians(k * 60 + t * 60)
            r1, r2 = s * 0.12, s * 0.33
            c.create_line(cx + r1 * math.cos(a), cy + r1 * math.sin(a),
                          cx + r2 * math.cos(a + 0.9), cy + r2 * math.sin(a + 0.9), fill=T.ACC, width=px(3),
                          capstyle="round")

    def _action_card(self, parent, icon, title, sub, cmd, col) -> None:
        f = tk.Frame(parent, bg=T.SURF, cursor="hand2", highlightthickness=1, highlightbackground=T.LINE)
        f.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else px(12), 0))
        parent.columnconfigure(col, weight=1, uniform="a")
        i = tk.Label(f, text=icon, bg=T.SURF, fg=T.ACC, font=T.font("big"), padx=px(16), pady=px(14))
        i.pack(side="left")
        tf = tk.Frame(f, bg=T.SURF)
        tf.pack(side="left", padx=(0, px(22)))
        a = tk.Label(tf, text=title, bg=T.SURF, fg=T.TEXT, font=T.font("h2"), anchor="w")
        a.pack(anchor="w")
        b = tk.Label(tf, text=sub, bg=T.SURF, fg=T.MUTE, font=T.font("small"), anchor="w")
        b.pack(anchor="w")
        widgets = (f, i, tf, a, b)

        def hover(on):
            start, end = (T.SURF, T.SURF2) if on else (T.SURF2, T.SURF)

            def step(t):
                c = T.lerp_color(start, end, t)
                for w in widgets:
                    w.configure(bg=c)
                f.configure(highlightbackground=T.lerp_color(T.LINE, T.ACC_DIM, t if on else 1 - t))

            T.animate(f, 150, step, key=("card", title))

        for w in widgets:
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))

    def on_show(self) -> None:
        for w in self.recent.winfo_children():
            w.destroy()
        recent = [r for r in self.app.settings.get("recent", []) if r][:6]
        shown = 0
        for r in recent:
            p = Path(r)
            if not p.is_dir():
                continue
            shown += 1
            row = tk.Frame(self.recent, bg=T.BG, cursor="hand2")
            row.pack(fill="x", pady=1)
            name = tk.Label(row, text=p.name or str(p), bg=T.BG, fg=T.TEXT, font=T.font("bold"), anchor="w",
                            padx=px(10), pady=px(6))
            name.pack(side="left")
            path = tk.Label(row, text=str(p.parent), bg=T.BG, fg=T.FAINT, font=T.font("small"), anchor="w")
            path.pack(side="left", fill="x", expand=True)
            n = _quick_count(p)
            cnt = tk.Label(row, text=f"{n} photos" if n >= 0 else "", bg=T.BG, fg=T.MUTE, font=T.font("small"),
                           padx=px(10))
            cnt.pack(side="right")
            ws = (row, name, path, cnt)
            for w in ws:
                w.bind("<Button-1>", lambda e, p=p: self.app.open_folder(p))
                w.bind("<Enter>", lambda e, ws=ws: [x.configure(bg=T.SURF) for x in ws])
                w.bind("<Leave>", lambda e, ws=ws: [x.configure(bg=T.BG) for x in ws])
        if not shown:
            tk.Label(self.recent, text="Folders you open will appear here.", bg=T.BG, fg=T.FAINT,
                     font=T.font("small"), anchor="w", padx=px(10)).pack(fill="x")
        self._tip_i = (self._tip_i + 1) % len(TIPS)
        self.tip.configure(text="Tip:  " + TIPS[self._tip_i])
        T.animate(self.logo, 900, self._draw_logo, key="logo")
        # slide the content up a touch as it appears
        T.animate(self.wrap, 320, lambda t: self.wrap.place_configure(rely=0.5 - 0.04 * t), key="welcome-in")
