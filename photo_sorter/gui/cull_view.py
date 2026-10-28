"""The main culling screen: filters | thumbnail grid (or loupe/compare) | inspector."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from ..core.constants import COLOR_LABELS
from ..core.library import SORT_KEYS, PhotoFilter, filter_and_sort, Photo
from . import theme as T
from .inspector import Inspector
from .thumbgrid import ThumbGrid
from .theme import px
from .viewer import CompareView, Loupe


class CullView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.filter = PhotoFilter()
        self.flag_var = tk.StringVar(value="all")
        self.rating_var = tk.IntVar(value=0)
        self.label_var = tk.StringVar(value="")
        self.sort_var = tk.StringVar(value=app.settings.get("sort", "Capture time"))
        self.search_var = tk.StringVar()
        self.blurry_var = tk.BooleanVar(value=False)
        self.grouped_var = tk.BooleanVar(value=False)
        self.collapse_var = tk.BooleanVar(value=False)
        self.size_var = tk.IntVar(value=int(app.settings.get("thumb_size", 190)))
        self.visible: List[Photo] = []

        self._build_filters()
        self.inspector = Inspector(self, app)
        self.inspector.pack(side="right", fill="y")
        tk.Frame(self, bg=T.LINE, width=1).pack(side="right", fill="y")
        self.center = tk.Frame(self, bg=T.GRID_BG)
        self.center.pack(side="left", fill="both", expand=True)
        # banner shown while viewing "photos similar to X"
        self.similar_to: Optional[Photo] = None
        self.banner = tk.Frame(self.center, bg=T.ACC_DIM)
        self.banner_label = tk.Label(self.banner, text="", bg=T.ACC_DIM, fg=T.TEXT, font=T.font("bold"),
                                     padx=px(12), pady=px(6), anchor="w")
        self.banner_label.pack(side="left", fill="x", expand=True)
        b = tk.Label(self.banner, text="✕  Back to all photos", bg=T.ACC_DIM, fg=T.TEXT, font=T.font("small"),
                     padx=px(12), cursor="hand2")
        b.pack(side="right", fill="y")
        b.bind("<Button-1>", lambda e: self.clear_similar())
        self._default_empty = "Open a folder of photos to get started.\n\nCtrl+O  or  drag a folder onto this window"
        self.grid = ThumbGrid(self.center, app.images, on_select=self._on_select, on_open=app.open_viewer,
                              on_context=self._context_menu, cell=self.size_var.get(),
                              empty_text=self._default_empty)
        self.grid.pack(fill="both", expand=True)
        self.loupe = Loupe(self.center, app)
        self.compare = CompareView(self.center, app)
        self.mode = "grid"

    # ------------------------------------------------------------ filter panel
    def _build_filters(self) -> None:
        panel = tk.Frame(self, bg=T.PANEL, width=px(212))
        panel.pack(side="left", fill="y")
        panel.pack_propagate(False)
        tk.Frame(self, bg=T.LINE, width=1).pack(side="left", fill="y")
        pad = {"padx": px(14), "fill": "x"}

        def head(text):
            tk.Label(panel, text=text.upper(), bg=T.PANEL, fg=T.FAINT, font=T.font("tiny"), anchor="w").pack(
                pady=(px(14), px(5)), **pad)

        head("Search")
        e = ttk.Entry(panel, textvariable=self.search_var)
        e.pack(**pad)
        self.search_entry = e
        T.tip(e, "Filter by file name, folder, camera or lens")
        self._search_job = None

        def on_search(*_a):
            if self._search_job:
                self.after_cancel(self._search_job)
            self._search_job = self.after(250, self.apply_filters)

        self.search_var.trace_add("write", on_search)

        head("Show")
        opts = [("all", "All"), ("picks", "Picks"), ("rejects", "Rejects"), ("unflagged", "Unflagged"),
                ("not_rejected", "Not rejected")]
        self.flag_buttons = {}
        self.flag_counts = {}
        box = tk.Frame(panel, bg=T.PANEL)
        box.pack(**pad)
        for v, t in opts:
            row = tk.Frame(box, bg=T.PANEL, cursor="hand2")
            row.pack(fill="x")
            b = tk.Label(row, text=t, bg=T.PANEL, fg=T.TEXT, font=T.font("body"), anchor="w", padx=px(8),
                         pady=px(3))
            b.pack(side="left", fill="x", expand=True)
            n = tk.Label(row, text="", bg=T.PANEL, fg=T.FAINT, font=T.font("small"), padx=px(8))
            n.pack(side="right")
            for w in (row, b, n):
                w.bind("<Button-1>", lambda e, v=v: (self.flag_var.set(v), self.apply_filters()))
            self.flag_buttons[v] = (row, b, n)
            self.flag_counts[v] = n
        self.flag_var.trace_add("write", lambda *a: self._paint_flag_buttons())
        self._paint_flag_buttons()

        head("Minimum stars")
        sr = tk.Frame(panel, bg=T.PANEL)
        sr.pack(**pad)
        self.star_filter = T.StarRating(sr, lambda n: (self.rating_var.set(n), self.star_filter.set(n),
                                                       self.apply_filters()), size="body")
        self.star_filter.pack(side="left")

        head("Colour label")
        lr = tk.Frame(panel, bg=T.PANEL)
        lr.pack(**pad)
        self.label_dots = {}
        for name in COLOR_LABELS:
            c = tk.Canvas(lr, width=px(22), height=px(22), bg=T.PANEL, highlightthickness=0, cursor="hand2")
            c.pack(side="left", padx=1)
            c.bind("<Button-1>", lambda e, n=name: (self.label_var.set("" if self.label_var.get() == n else n),
                                                    self._paint_labels(), self.apply_filters()))
            self.label_dots[name] = c
        self._paint_labels()

        head("Refine")
        for var, text, tiptext in (
            (self.blurry_var, "Only blurry", "Photos below your sharpness threshold"),
            (self.grouped_var, "Only bursts / groups", "Photos that belong to a burst or similar-shot group"),
            (self.collapse_var, "Collapse groups to best", "Show just the best frame of each group"),
        ):
            cb = ttk.Checkbutton(panel, text=text, variable=var, style="Panel.TCheckbutton", command=self.apply_filters)
            cb.pack(anchor="w", padx=px(14))
            T.tip(cb, tiptext)

        head("Sort by")
        cb = ttk.Combobox(panel, textvariable=self.sort_var, values=list(SORT_KEYS.keys()), state="readonly")
        cb.pack(**pad)
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        head("Thumbnail size")
        sc = ttk.Scale(panel, from_=120, to=360, variable=self.size_var, command=self._on_size)
        sc.pack(**pad)

        self.count_label = tk.Label(panel, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"), anchor="w",
                                    justify="left")
        self.count_label.pack(pady=(px(16), 0), **pad)
        ttk.Button(panel, text="Reset filters", style="PanelGhost.TButton", command=self.reset_filters).pack(
            anchor="w", padx=px(8), pady=px(4))

        tk.Label(panel, text="P pick · X reject · U clear\n1-5 stars · 6-9 labels\nSpace loupe · C compare\n"
                             "Ctrl+Z undo · ? all shortcuts",
                 bg=T.PANEL, fg=T.FAINT, font=T.font("tiny"), justify="left", anchor="w").pack(
            side="bottom", fill="x", padx=px(14), pady=px(12))

    def _paint_flag_buttons(self) -> None:
        for v, (row, b, n) in self.flag_buttons.items():
            on = self.flag_var.get() == v
            bg = T.SURF2 if on else T.PANEL
            row.configure(bg=bg)
            b.configure(bg=bg, fg=T.ACC if on else T.TEXT)
            n.configure(bg=bg)

    def update_flag_counts(self) -> None:
        lib = self.app.lib
        if lib is None:
            for n in self.flag_counts.values():
                n.configure(text="")
            return
        c = lib.counts()
        for v, k in (("all", "total"), ("picks", "picks"), ("rejects", "rejects"), ("unflagged", "unflagged")):
            self.flag_counts[v].configure(text=str(c[k]))
        self.flag_counts["not_rejected"].configure(text=str(c["total"] - c["rejects"]))

    def _paint_labels(self) -> None:
        for name, c in self.label_dots.items():
            c.delete("all")
            col = T.LABEL_COLORS[name]
            on = self.label_var.get() == name
            c.create_oval(px(4), px(4), px(18), px(18), fill=col, outline=T.TEXT if on else col, width=px(2) if on else 1)

    def _on_size(self, _v=None) -> None:
        size = int(self.size_var.get())
        if abs(size - self.grid.cell) >= 6:
            self.app.settings["thumb_size"] = size
            self.grid.set_cell_size(size)

    def reset_filters(self) -> None:
        self.flag_var.set("all")
        self.rating_var.set(0)
        self.star_filter.set(0)
        self.label_var.set("")
        self._paint_labels()
        self.search_var.set("")
        for v in (self.blurry_var, self.grouped_var, self.collapse_var):
            v.set(False)
        self.apply_filters()

    # ------------------------------------------------------------ data
    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def show_similar(self, p: Photo) -> None:
        lib = self.app.lib
        if lib is None or not p.analyzed:
            return self.app.toast("Analyse the folder first.", "warn")
        matches = lib.find_similar(p)
        if self.mode != "grid":
            self.close_viewer()
        self.similar_to = p
        self.visible = [p] + [m for _s, m in matches]
        self.banner_label.configure(text=f"Photos that look like {p.name}  ·  {len(matches)} found, most similar first")
        self.banner.pack(fill="x", before=self.grid)
        self.grid.set_photos(self.visible, keep_scroll=False)
        self.grid.select(p)
        self.count_label.configure(text=f"Showing {len(self.visible)} similar")
        if not matches:
            self.app.toast("No similar photos found in this folder.", "info")

    def clear_similar(self) -> None:
        self.similar_to = None
        self.banner.pack_forget()
        self.apply_filters(keep_scroll=False)

    def apply_filters(self, keep_scroll: bool = True) -> None:
        lib = self.app.lib
        if self.similar_to is not None:
            self.similar_to = None
            self.banner.pack_forget()
        f = self.filter
        f.flag = self.flag_var.get()
        f.min_rating = self.rating_var.get()
        f.label = self.label_var.get() or None
        f.text = self.search_var.get().strip()
        f.only_blurry = self.blurry_var.get()
        f.only_grouped = self.grouped_var.get()
        f.hide_non_best = self.collapse_var.get()
        self.app.settings["sort"] = self.sort_var.get()
        photos = lib.photos if lib else []
        self.visible = filter_and_sort(photos, f, self.sort_var.get())
        total = len(photos)
        if not total:
            self.grid.empty_text = self._default_empty
        else:
            self.grid.empty_text = "No photos match these filters.\n\nPress Reset filters on the left to see them all."
        self.grid.set_photos(self.visible, keep_scroll=keep_scroll)
        self.count_label.configure(text=f"Showing {len(self.visible)} of {total}" if total else "")
        self.update_flag_counts()
        if self.grid.current is None and self.visible:
            self.grid.select(self.visible[0], notify=True)
        elif self.grid.current is not None:
            self.inspector.show(self.grid.current, self.grid.selected_photos())
        else:
            self.inspector.show(None, [])

    def refresh(self, keys=None) -> None:
        """Redraw after decisions changed (no re-filtering, so items don't jump away).

        ``keys`` limits the grid redraw to those photos' cells."""
        if keys is None:
            self.grid.redraw()
        else:
            self.grid.redraw_keys(keys)
        self.update_flag_counts()
        self.inspector.show(self.grid.current, self.grid.selected_photos())
        if self.mode == "loupe":
            self.loupe.refresh()
        elif self.mode == "compare":
            self.compare.refresh()

    def _on_select(self, current: Optional[Photo], selection: List[Photo]) -> None:
        self.inspector.show(current, selection)
        if self.mode == "loupe" and current is not None:
            self._show_loupe(current)

    def selection(self) -> List[Photo]:
        if self.mode == "compare":
            p = self.compare.active_photo()
            return [p] if p else []
        if self.mode == "loupe" and self.grid.current is not None:
            return [self.grid.current]
        return self.grid.selected_photos()

    # ------------------------------------------------------------ viewer modes
    def open_loupe(self, photo: Photo) -> None:
        if photo.key not in self.grid.index:
            self.reset_filters()
        self.grid.focus_photo(photo)
        self.grid.pack_forget()
        self.compare.pack_forget()
        self.loupe.pack(fill="both", expand=True)
        self.mode = "loupe"
        self._show_loupe(photo)
        self.loupe.pane.focus_set()

    def _show_loupe(self, photo: Photo) -> None:
        idx = self.grid.index.get(photo.key, 0)
        self.loupe.show(photo, idx, len(self.grid.order))
        self.loupe.strip.set(self.grid.order, idx)
        for d in (1, -1, 2):
            j = idx + d
            if 0 <= j < len(self.grid.order):
                self.app.images.prefetch(self.grid.order[j])

    def open_compare(self, photos: List[Photo]) -> None:
        if len(photos) < 2:
            self.app.toast("Select 2-4 photos to compare (Ctrl+click or Shift+click).", "warn")
            return
        self.grid.pack_forget()
        self.loupe.pack_forget()
        self.compare.pack(fill="both", expand=True)
        self.mode = "compare"
        self.compare.show(photos)
        self.inspector.show(photos[0], [photos[0]])

    def close_viewer(self) -> None:
        self.loupe.pack_forget()
        self.compare.pack_forget()
        self.grid.pack(fill="both", expand=True)
        self.mode = "grid"
        if self.grid.current is not None:
            self.grid.ensure_visible(self.grid.index.get(self.grid.current.key, 0))
        self.grid.canvas.focus_set()
        self.refresh()

    # ------------------------------------------------------------ context menu
    def _context_menu(self, e, p: Photo) -> None:
        app = self.app
        sel = self.grid.selected_photos()
        m = tk.Menu(self, tearoff=0, bg=T.SURF, fg=T.TEXT, activebackground=T.ACC_DIM, activeforeground=T.TEXT,
                    bd=0)
        m.add_command(label="Open in loupe        Space", command=lambda: app.open_viewer(p))
        m.add_command(label="Find similar photos", command=lambda: (app.switch_view("cull"), self.show_similar(p)))
        if len(sel) >= 2:
            m.add_command(label=f"Compare {min(4, len(sel))} photos      C", command=lambda: app.open_compare(sel[:4]))
        m.add_separator()
        m.add_command(label="Pick        P", command=lambda: app.decide_flag("pick"))
        m.add_command(label="Reject      X", command=lambda: app.decide_flag("reject"))
        m.add_command(label="Clear flag  U", command=lambda: app.decide_flag(None))
        rate = tk.Menu(m, tearoff=0, bg=T.SURF, fg=T.TEXT, activebackground=T.ACC_DIM)
        for n in range(0, 6):
            rate.add_command(label=("★" * n) or "No stars", command=lambda n=n: app.decide_rating(n))
        m.add_cascade(label="Rating", menu=rate)
        lab = tk.Menu(m, tearoff=0, bg=T.SURF, fg=T.TEXT, activebackground=T.ACC_DIM)
        for name in COLOR_LABELS:
            lab.add_command(label=name, command=lambda n=name: app.decide_label(n))
        lab.add_command(label="None", command=lambda: app.decide_label(None))
        m.add_cascade(label="Colour label", menu=lab)
        if len(sel) >= 2:
            m.add_command(label="Keep best, reject the rest", command=lambda: app.keep_best_of(sel))
        if p.group_id:
            m.add_separator()
            m.add_command(label=f"View group #{p.group_id}", command=lambda: app.show_group(p.group_id))
            if not p.is_best:
                m.add_command(label="Make this the best of its group", command=lambda: app.make_best(p))
            m.add_command(label="Remove from group", command=lambda: app.remove_from_group(p))
        m.add_separator()
        n_sel = max(1, len(sel))
        m.add_command(label=f"Save {n_sel} as JPG…   Ctrl+J",
                      command=lambda: app.save_selected_as_jpg(sel or [p]))
        m.add_command(label="Open with default app", command=lambda: app.open_external(p))
        m.add_command(label="Show in folder", command=lambda: app.reveal(p))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
