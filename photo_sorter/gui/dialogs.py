"""Settings and About windows."""

from __future__ import annotations

import os
import platform
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import __version__
from ..core import analysis
from ..core import file_operations as fo
from ..core.image_loader import HAS_HEIF, HAS_RAWPY
from . import theme as T
from .theme import px

APP_DATA = Path.home() / ".photo_sorter"


class _Dialog(tk.Toplevel):
    def __init__(self, app, title: str):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.configure(bg=T.SURF)
        self.transient(app)
        self.resizable(False, False)
        self.bind("<Escape>", lambda e: self.destroy())
        try:
            self.iconphoto(False, app._icon_img)
        except Exception:
            pass

    def center(self) -> None:
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.focus_set()


class SettingsDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "Settings")
        s = app.settings
        body = tk.Frame(self, bg=T.SURF)
        body.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        def section(text):
            tk.Label(body, text=text.upper(), bg=T.SURF, fg=T.FAINT, font=T.font("tiny"), anchor="w").pack(
                fill="x", pady=(px(12), px(4)))

        self.vars = {}

        def check(key, text, default=True):
            v = tk.BooleanVar(value=s.get(key, default))
            ttk.Checkbutton(body, text=text, variable=v, style="Card.TCheckbutton").pack(anchor="w")
            self.vars[key] = v

        section("General")
        check("reopen_last", "Reopen the last folder on start")
        check("auto_analyze", "Analyse new photos automatically when a folder opens")
        check("auto_advance", "Auto-advance after pick / reject / rating")
        check("animations", "Animations")

        section("Performance")
        r = tk.Frame(body, bg=T.SURF)
        r.pack(fill="x")
        tk.Label(r, text="Analysis threads", bg=T.SURF, fg=T.TEXT).pack(side="left")
        self.threads = tk.IntVar(value=int(s.get("threads", 0) or 0))
        ttk.Spinbox(r, from_=0, to=32, width=4, textvariable=self.threads).pack(side="right")
        tk.Label(body, text=f"0 = automatic ({analysis.DEFAULT_WORKERS} on this computer). Lower it to keep the "
                            "computer responsive while analysing.", bg=T.SURF, fg=T.MUTE, font=T.font("small"),
                 justify="left", wraplength=px(380), anchor="w").pack(fill="x")

        section("Data")
        r = tk.Frame(body, bg=T.SURF)
        r.pack(fill="x")
        ttk.Button(r, text="Open settings & logs folder", style="Small.TButton",
                   command=lambda: (APP_DATA.mkdir(exist_ok=True), fo.open_in_file_manager(APP_DATA))).pack(side="left")
        if app.lib is not None:
            ttk.Button(r, text="Re-analyse this folder", style="Small.TButton",
                       command=lambda: (self.destroy(), app.start_analysis(force=True))).pack(side="left", padx=px(6))
        tk.Label(body, text="Each photo folder keeps its analysis and your decisions in a hidden "
                            ".photo_sorter_cache folder, so they travel with the photos.", bg=T.SURF, fg=T.MUTE,
                 font=T.font("small"), justify="left", wraplength=px(380), anchor="w").pack(fill="x", pady=(px(6), 0))

        btns = tk.Frame(self, bg=T.SURF)
        btns.pack(fill="x", padx=px(22), pady=(0, px(18)))
        ttk.Button(btns, text="About", style="Small.TButton", command=lambda: AboutDialog(app)).pack(side="left")
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=px(8))
        self.center()

    def save(self) -> None:
        s = self.app.settings
        for k, v in self.vars.items():
            s[k] = bool(v.get())
        try:
            s["threads"] = max(0, int(self.threads.get()))
        except (tk.TclError, ValueError):
            s["threads"] = 0
        self.app.auto_advance.set(s["auto_advance"])
        T.ANIMATIONS_ON = s["animations"]
        T.save_settings(s)
        self.destroy()
        self.app.toast("Settings saved.", "ok")


class AboutDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "About Photography Tools")
        body = tk.Frame(self, bg=T.SURF)
        body.pack(fill="both", expand=True, padx=px(26), pady=px(22))
        tk.Label(body, text="Photography Tools", bg=T.SURF, fg=T.TEXT, font=T.font("big")).pack(anchor="w")
        tk.Label(body, text=f"Version {__version__}", bg=T.SURF, fg=T.ACC, font=T.font("bold")).pack(anchor="w")
        tk.Label(body, text="Cull, group, organise and deliver photo shoots.\nMade by Demi. Free to use and share "
                            "(MIT licence).", bg=T.SURF, fg=T.MUTE, justify="left").pack(anchor="w", pady=(px(8), px(12)))
        info = [
            ("RAW support", "Yes (rawpy)" if HAS_RAWPY else "Off - install rawpy"),
            ("HEIC support", "Yes" if HAS_HEIF else "Off - install pillow-heif"),
            ("Python", platform.python_version()),
            ("System", f"{platform.system()} {platform.release()}"),
            ("Settings", str(APP_DATA)),
        ]
        for k, v in info:
            r = tk.Frame(body, bg=T.SURF)
            r.pack(fill="x")
            tk.Label(r, text=k, bg=T.SURF, fg=T.MUTE, width=14, anchor="w", font=T.font("small")).pack(side="left")
            tk.Label(r, text=v, bg=T.SURF, fg=T.TEXT, anchor="w", font=T.font("small")).pack(side="left")
        ttk.Button(body, text="Close", command=self.destroy).pack(anchor="e", pady=(px(16), 0))
        self.center()
