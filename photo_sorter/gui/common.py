"""Small building blocks shared by the secondary screens."""

from __future__ import annotations

import tkinter as tk

from . import theme as T
from .theme import px


def header(parent, title: str, subtitle: str = "") -> tk.Frame:
    f = tk.Frame(parent, bg=T.BG)
    f.pack(fill="x", padx=px(24), pady=(px(18), px(8)))
    tk.Label(f, text=title, bg=T.BG, fg=T.TEXT, font=T.font("h1"), anchor="w").pack(fill="x")
    if subtitle:
        tk.Label(f, text=subtitle, bg=T.BG, fg=T.MUTE, font=T.font("body"), anchor="w", justify="left",
                 wraplength=px(900)).pack(fill="x", pady=(px(2), 0))
    return f

