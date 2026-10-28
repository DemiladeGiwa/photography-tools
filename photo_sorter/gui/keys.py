"""Keyboard shortcuts for the main window (the list shown by ``?`` and the global key handler)."""

from __future__ import annotations

import tkinter as tk

from .. import __version__
from ..core.library import FLAG_PICK, FLAG_REJECT
from . import theme as T
from .theme import px

SHORTCUTS = [
    ("P", "Pick"), ("X  or  Delete", "Reject"), ("U", "Clear pick/reject"),
    ("1 – 5", "Star rating"), ("0", "Remove stars"), ("6 / 7 / 8 / 9", "Red / Yellow / Green / Blue label"),
    ("← → ↑ ↓", "Move through photos"), ("Home / End", "First / last photo"), ("Page Up / Down", "Scroll a page"),
    ("Space  or  Enter", "Open / close the loupe"),
    ("Z  or  click", "Zoom to 100% / fit"), ("H", "Focus map (sharpness heat-map)"), ("K", "Clipping warning"),
    ("F", "Show / hide the filmstrip in the loupe"),
    ("C", "Compare selected photos (2-4)"), ("Tab", "Next photo in Compare"), ("Esc", "Back to the grid"),
    ("Ctrl+Click / Shift+Click", "Select several photos"), ("Ctrl+A", "Select all"),
    ("Ctrl+Z", "Undo last decision"), ("Ctrl+O", "Open folder"), ("Ctrl+F", "Search"),
    ("Ctrl+I", "Import from card"), ("Ctrl+J", "Save selected as JPG"), ("F5", "Analyse"), ("F11", "Full screen"),
    ("Ctrl+,", "Settings"),
]

_TEXT_INPUTS = ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox", "Text", "Listbox")
_LABEL_KEYS = {"6": "Red", "7": "Yellow", "8": "Green", "9": "Blue"}


def handle_key(app, e) -> None:
    """Global key handler bound to the main window."""
    w = e.widget
    if not isinstance(w, tk.Misc):  # e.g. a combobox drop-down list
        return
    try:
        cls = w.winfo_class()
        if w.winfo_toplevel() is not app:
            return
    except Exception:
        return
    if cls in _TEXT_INPUTS:
        return
    ks = e.keysym
    if e.state & 0x4:
        _ctrl_key(app, ks)
        return
    if ks == "F11":
        app.toggle_fullscreen()
    elif ks == "F5":
        app.start_analysis()
    elif ks in ("question", "F1"):
        show_shortcuts(app)
    elif app.current_view in ("cull", "groups") and app.lib is not None:
        _culling_key(app, ks, shift=bool(e.state & 0x1))


def _ctrl_key(app, ks: str) -> None:
    low = ks.lower()
    if low == "z":
        app.undo_decision()
    elif low == "o":
        app.open_folder()
    elif low == "a" and app.current_view in ("cull", "groups") and app.lib:
        app.views[app.current_view].grid.select_all()
    elif low == "f" and app.lib:
        app.switch_view("cull")
        app.views["cull"].focus_search()
    elif low == "i":
        app.switch_view("import")
    elif low == "j" and app.current_view in ("cull", "groups") and app.lib:
        app.save_selected_as_jpg()
    elif ks == "comma":
        app.show_settings()


def _culling_key(app, ks: str, shift: bool) -> None:
    view = app.views[app.current_view]
    cull = app.views["cull"]
    mode = cull.mode if app.current_view == "cull" else "grid"
    low = ks.lower()
    if low == "p":
        app.decide_flag(FLAG_PICK)
    elif low == "x" or ks == "Delete":
        app.decide_flag(FLAG_REJECT)
    elif low == "u":
        app.decide_flag(None)
    elif ks in "012345" and len(ks) == 1:
        app.decide_rating(int(ks))
    elif ks in _LABEL_KEYS:
        app.decide_label(_LABEL_KEYS[ks])
    elif ks == "Escape":
        if mode != "grid":
            cull.close_viewer()
    elif ks in ("space", "Return"):
        if mode == "grid":
            if view.grid.current is not None:
                app.open_viewer(view.grid.current)
        else:
            cull.close_viewer()
    elif mode == "compare":
        if ks in ("Tab", "Right"):
            cull.compare.cycle(1)
            app.on_viewer_active_changed(cull.compare.active_photo())
        elif ks == "Left":
            cull.compare.cycle(-1)
            app.on_viewer_active_changed(cull.compare.active_photo())
        elif low == "z":
            cull.compare.panes[cull.compare.active].toggle_zoom()
    elif ks in ("Left", "Right", "Up", "Down"):
        g = view.grid
        if mode == "loupe":
            g.move(dx=-1 if ks in ("Left", "Up") else 1)
        else:
            g.move(dx={"Left": -1, "Right": 1}.get(ks, 0), dy={"Up": -1, "Down": 1}.get(ks, 0), extend=shift)
    elif low == "c":
        app.open_compare(view.grid.selected_photos()[:4])
    elif mode == "loupe" and low == "z":
        cull.loupe.pane.toggle_zoom()
    elif mode == "loupe" and low == "h":
        cull.loupe.set_overlay("heatmap")
    elif mode == "loupe" and low == "k":
        cull.loupe.set_overlay("clip")
    elif mode == "loupe" and low == "f":
        cull.loupe.toggle_filmstrip()
    elif ks == "Home" and view.grid.order:
        view.grid.select(view.grid.order[0])
    elif ks == "End" and view.grid.order:
        view.grid.select(view.grid.order[-1])
    elif ks in ("Prior", "Next"):
        rows = max(1, view.grid.canvas.winfo_height() // view.grid.cell_h)
        for _ in range(rows):
            view.grid.move(dy=-1 if ks == "Prior" else 1)


def show_shortcuts(app) -> None:
    win = tk.Toplevel(app)
    win.title("Keyboard shortcuts")
    win.configure(bg=T.SURF)
    win.transient(app)
    tk.Label(win, text="Keyboard shortcuts", bg=T.SURF, fg=T.TEXT, font=T.font("h2")).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=px(20), pady=(px(18), px(10)))
    for i, (k, d) in enumerate(SHORTCUTS, start=1):
        tk.Label(win, text=k, bg=T.SURF2, fg=T.ACC, font=T.font("mono"), padx=px(8), pady=px(2)).grid(
            row=i, column=0, sticky="e", padx=(px(20), px(10)), pady=px(2))
        tk.Label(win, text=d, bg=T.SURF, fg=T.TEXT, anchor="w").grid(row=i, column=1, sticky="w", padx=(0, px(24)))
    tk.Label(win, text=f"Photography Tools v{__version__}", bg=T.SURF, fg=T.FAINT, font=T.font("tiny")).grid(
        row=len(SHORTCUTS) + 1, column=0, columnspan=2, pady=px(14))
    win.bind("<Escape>", lambda e: win.destroy())
    win.focus_set()
