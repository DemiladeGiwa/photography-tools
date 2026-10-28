"""Groups screen: bursts, similar shots and duplicates, each with its best frame."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from ..core import grouping
from ..core.library import FLAG_PICK, FLAG_REJECT, Photo
from . import theme as T
from .inspector import Inspector
from .thumbgrid import ThumbGrid
from .theme import px

KIND_NAMES = {"burst": "Burst", "similar": "Similar shots", "duplicate": "Duplicates"}


class GroupsView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        top = tk.Frame(self, bg=T.PANEL)
        top.pack(fill="x")
        tk.Label(top, text="Bursts & similar shots", bg=T.PANEL, fg=T.TEXT, font=T.font("h2")).pack(
            side="left", padx=(px(18), px(10)), pady=px(10))
        self.summary = tk.Label(top, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"))
        self.summary.pack(side="left")
        right = tk.Frame(top, bg=T.PANEL)
        right.pack(side="right", padx=px(12))
        ttk.Button(right, text="Keep best of every group", style="Accent.TButton",
                   command=self.keep_best_all).pack(side="right", padx=(px(8), 0))
        ttk.Button(right, text="Regroup", command=self.regroup).pack(side="right", padx=(px(8), 0))
        self.across_var = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(right, text="Match across whole shoot", variable=self.across_var,
                             style="Panel.TCheckbutton")
        cb.pack(side="right", padx=px(8))
        T.tip(cb, "Also finds near-identical shots taken far apart in time (re-shoots, duplicates, copies).")
        self.window_var = tk.IntVar(value=90)
        sp = ttk.Spinbox(right, from_=5, to=900, increment=5, width=5, textvariable=self.window_var)
        sp.pack(side="right")
        tk.Label(right, text="Look within (s)", bg=T.PANEL, fg=T.MUTE, font=T.font("small")).pack(side="right", padx=px(4))
        T.tip(sp, "How far apart in time two shots can be and still count as the same moment.")
        self.sens_var = tk.StringVar(value="Balanced")
        T.Segmented(right, self.sens_var, [(k, k) for k in grouping.SENSITIVITY_THRESHOLDS]).pack(
            side="right", padx=px(10))

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)
        self.inspector = Inspector(body, app)
        self.inspector.pack(side="right", fill="y")
        tk.Frame(body, bg=T.LINE, width=1).pack(side="right", fill="y")
        self.grid = ThumbGrid(body, app.images, on_select=lambda cur, sel: self.inspector.show(cur, sel),
                              on_open=app.open_viewer, cell=170,
                              empty_text="No bursts or similar shots found yet.\nAnalyse a folder, or try 'Loose'.")
        self.grid.pack(side="left", fill="both", expand=True)
        self.grid.header_click = self._header_clicked
        self.grid.on_context = app.views["cull"]._context_menu if "cull" in app.views else None
        self._gid_of_header = {}

    def on_show(self) -> None:
        lib = self.app.lib
        if lib:
            self.sens_var.set(lib.settings.group_sensitivity)
            self.window_var.set(int(lib.settings.group_time_window))
            self.across_var.set(bool(lib.settings.group_across_shoot))
        self.populate()
        if self.grid.on_context is None and "cull" in self.app.views:
            self.grid.on_context = self.app.views["cull"]._context_menu

    def populate(self, focus_gid: Optional[int] = None) -> None:
        lib = self.app.lib
        sections = []
        self._gid_of_header = {}
        n_photos = 0
        if lib:
            for g in lib.groups:
                members = lib.group_members(g.id)
                n_photos += len(members)
                picks = sum(1 for m in members if m.flag == FLAG_PICK)
                best = lib.by_key.get(g.best)
                title = (f"Group {g.id}  ·  {KIND_NAMES.get(g.kind, g.kind)}  ·  {len(members)} photos",
                         f"{picks} picked  ·  best: {best.name if best else '-'}  ·  match {g.cohesion * 100:.0f}%")
                self._gid_of_header[title] = g.id
                sections.append((title, members))
        self.grid.set_sections(sections, keep_scroll=focus_gid is None)
        n = len(lib.groups) if lib else 0
        self.summary.configure(text=f"{n} groups · {n_photos} photos" if lib else "")
        if focus_gid is not None and lib:
            members = lib.group_members(focus_gid)
            if members:
                self.grid.select(members[0])
        elif self.grid.current is None and sections:
            self.grid.select(sections[0][1][0])
        else:
            self.inspector.show(self.grid.current, self.grid.selected_photos())

    def refresh(self) -> None:
        self.grid.redraw()
        self.inspector.show(self.grid.current, self.grid.selected_photos())

    def selection(self) -> List[Photo]:
        return self.grid.selected_photos()

    def _header_clicked(self, title) -> None:
        gid = self._gid_of_header.get(title)
        if gid is not None:
            members = self.app.lib.group_members(gid)
            if members:
                self.grid.select(members[0])
                self.grid.selected = {m.key: m for m in members}
                self.grid.redraw()
                self.inspector.show(members[0], members)

    def regroup(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        lib.settings.group_sensitivity = self.sens_var.get()
        try:
            lib.settings.group_time_window = float(self.window_var.get())
        except (tk.TclError, ValueError):
            pass
        lib.settings.group_across_shoot = bool(self.across_var.get())
        lib.mark_dirty()

        def work(progress, stop):
            lib.compute_groups()
            return len(lib.groups)

        def done(n):
            self.populate()
            self.app.refresh_all()
            self.app.toast(f"Found {n} groups.", "ok")

        self.app.run_task("Grouping photos…", work, done)

    def keep_best_all(self) -> None:
        lib = self.app.lib
        if not lib or not lib.groups:
            return
        changed = [p for g in lib.groups for p in lib.group_members(g.id)]
        manual = [p for p in changed if p.touched]
        msg = (f"Pick the best photo of each of the {len(lib.groups)} groups and reject the other "
               f"{len(changed) - len(lib.groups)} frames?")
        if manual:
            msg += f"\n\n{len(manual)} photos you already decided by hand will be left as they are."
        if not messagebox.askyesno("Keep best of every group", msg, parent=self):
            return
        self.app.push_undo(changed)
        for g in lib.groups:
            for p in lib.group_members(g.id):
                if p.touched:
                    continue
                p.flag = FLAG_PICK if p.key == g.best else FLAG_REJECT
        lib.mark_dirty()
        self.app.refresh_all()
        self.app.toast_undo("Best frames picked, the rest rejected.")
