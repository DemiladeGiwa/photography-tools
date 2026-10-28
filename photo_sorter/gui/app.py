"""Photography Tools - main window.

One window, one shared library: open a folder once, it's analysed once, and
every screen (Cull, Groups, Auto-Cull, Organise, Export, History) works on
the same photos and decisions.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional

from ..core import file_operations as fo
from ..core.image_loader import HAS_RAWPY
from ..core.library import FLAG_PICK, FLAG_REJECT, Library, Photo
from . import keys
from . import theme as T
from .images import ImageService
from .theme import px

NAV = [
    ("import", "⤓", "Import"),
    ("cull", "▦", "Cull"),
    ("groups", "❏", "Groups"),
    ("auto", "✦", "Auto-Cull"),
    ("insights", "◔", "Insights"),
    ("organize", "⇄", "Organise"),
    ("export", "⇪", "Export"),
    ("history", "↺", "History"),
]
# Screens that need an open folder; without one the Welcome screen is shown instead.
NEEDS_LIBRARY = {"cull", "groups", "auto", "insights", "organize", "export", "history"}

class App(tk.Tk):
    def __init__(self, folder: Optional[Path] = None, start_view: str = "cull"):
        T.enable_high_dpi()
        super().__init__()
        T.setup(self)
        self.title("Photography Tools")
        self.settings = T.load_settings()
        T.ANIMATIONS_ON = self.settings.get("animations", True)
        self._set_icon()
        if self.settings.get("animations", True):
            T.fade_in_window(self)
        if "recent" not in self.settings:
            self.settings["recent"] = T._legacy_recent()
        geo = self.settings.get("geometry")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if not geo:
            w, h = min(px(1440), sw - px(40)), min(px(900), sh - px(80))
            geo = f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}"
        self.geometry(geo)
        self.minsize(px(1000), px(640))
        if self.settings.get("zoomed") and sys.platform == "win32":
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

        self.lib: Optional[Library] = None
        self.images = ImageService(self)
        self.undo_stack: List[list] = []
        self._task_thread: Optional[threading.Thread] = None
        self._task_stop = False
        self._task_q: "queue.Queue[tuple]" = queue.Queue()
        self._analysis_updates: set = set()
        self._last_live_refresh = 0.0
        self.auto_advance = tk.BooleanVar(value=self.settings.get("auto_advance", True))
        self.recursive_var = tk.BooleanVar(value=self.settings.get("recursive", False))

        self._build_topbar()
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self.content = tk.Frame(body, bg=T.BG)
        self.content.pack(side="left", fill="both", expand=True)

        from .cull_view import CullView
        from .import_view import ImportView
        from .insights_view import InsightsView
        from .auto_view import AutoCullView
        from .export_view import ExportView
        from .groups_view import GroupsView
        from .history_view import HistoryView
        from .organize_view import OrganizeView
        from .welcome import WelcomeView

        self.views: Dict[str, tk.Frame] = {}
        self.views["welcome"] = WelcomeView(self.content, self)
        self.views["import"] = ImportView(self.content, self)
        self.views["insights"] = InsightsView(self.content, self)
        self.views["cull"] = CullView(self.content, self)
        self.views["groups"] = GroupsView(self.content, self)
        self.views["auto"] = AutoCullView(self.content, self)
        self.views["organize"] = OrganizeView(self.content, self)
        self.views["export"] = ExportView(self.content, self)
        self.views["history"] = HistoryView(self.content, self)
        self.current_view = ""
        self._shown_frame: Optional[tk.Frame] = None
        self.switch_view(start_view if start_view in self.views else "cull")

        self.bind_all("<KeyPress>", lambda e: keys.handle_key(self, e), add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._setup_drop()
        self.after(100, self._poll_task)
        self._update_analyse_hint()
        self.after(5000, self._autosave)
        if folder:
            self.after(50, lambda: self.open_folder(Path(folder)))
        elif self.settings.get("reopen_last", True) and self.settings.get("recent"):
            last = Path(self.settings["recent"][0])
            if last.is_dir():
                self.after(50, lambda: self.open_folder(last))
        self.report_callback_exception = self._report_exception

    # ================================================================ layout
    def _build_topbar(self) -> None:
        bar = tk.Frame(self, bg=T.PANEL, height=px(52))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="◉", bg=T.PANEL, fg=T.ACC, font=T.font("h1")).pack(side="left", padx=(px(16), px(4)))
        tk.Label(bar, text="Photography Tools", bg=T.PANEL, fg=T.TEXT, font=T.font("h2")).pack(side="left")
        tk.Frame(bar, bg=T.LINE, width=1).pack(side="left", fill="y", padx=px(14), pady=px(12))
        ttk.Button(bar, text="Open folder…", command=self.open_folder).pack(side="left")
        self.folder_btn = tk.Label(bar, text="No folder open  ▾", bg=T.PANEL, fg=T.MUTE, font=T.font("body"),
                                   cursor="hand2", padx=px(10))
        self.folder_btn.pack(side="left")
        self.folder_btn.bind("<Button-1>", self._recent_menu)
        T.tip(self.folder_btn, "Recent folders")
        cb = ttk.Checkbutton(bar, text="Include subfolders", variable=self.recursive_var, style="Panel.TCheckbutton",
                             command=lambda: self.set_recursive(self.recursive_var.get()))
        cb.pack(side="left", padx=px(6))

        right = tk.Frame(bar, bg=T.PANEL)
        right.pack(side="right", padx=px(12))
        self.counts_frame = tk.Frame(right, bg=T.PANEL)
        self.counts_frame.pack(side="right", padx=(px(12), 0))
        self.count_labels: Dict[str, tk.Label] = {}
        for i, (flt, tiptext) in enumerate((("all", "Show all photos"), ("picks", "Show picks"),
                                            ("rejects", "Show rejects"), ("unflagged", "Show photos to review"))):
            if i:
                tk.Label(self.counts_frame, text="·", bg=T.PANEL, fg=T.FAINT, font=T.font("small")).pack(side="left")
            lab = tk.Label(self.counts_frame, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"), cursor="hand2",
                           padx=px(5))
            lab.pack(side="left")
            lab.bind("<Button-1>", lambda e, f=flt: self.show_flag_filter(f))
            lab.bind("<Enter>", lambda e, lab=lab: lab.configure(fg=T.TEXT))
            lab.bind("<Leave>", lambda e, lab=lab: lab.configure(fg=T.MUTE))
            T.tip(lab, tiptext)
            self.count_labels[flt] = lab
        aa = ttk.Checkbutton(right, text="Auto-advance", variable=self.auto_advance, style="Panel.TCheckbutton",
                             command=lambda: self.settings.__setitem__("auto_advance", self.auto_advance.get()))
        aa.pack(side="right", padx=px(6))
        T.tip(aa, "Jump to the next photo after you pick, reject or rate one.")

        self.task_frame = tk.Frame(bar, bg=T.PANEL)
        self.task_frame.pack(side="left", fill="x", expand=True, padx=px(16))
        self.analyse_btn = ttk.Button(self.task_frame, text="Analyse", style="Accent.TButton", command=self.start_analysis)
        self.analyse_btn.pack(side="left")
        T.tip(self.analyse_btn, "Measure sharpness, exposure and similarity for every photo (F5). Results are "
                                "cached, so re-opening a folder is instant.")
        self.stop_btn = ttk.Button(self.task_frame, text="Stop", style="Small.TButton", command=self.stop_task)
        self.progress = ttk.Progressbar(self.task_frame, mode="determinate", length=px(180))
        self.task_label = tk.Label(self.task_frame, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"))
        self.task_label.pack(side="left", padx=px(10))
        if not HAS_RAWPY:
            w = tk.Label(bar, text="⚠ RAW support off", bg=T.PANEL, fg=T.WARN, font=T.font("small"))
            w.pack(side="right")
            T.tip(w, "Install rawpy to read RAW files directly:  pip install rawpy\nRAW files with a matching JPEG "
                     "still work (the JPEG is used for previews).")
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x")

    def _build_sidebar(self, parent) -> None:
        side = tk.Frame(parent, bg=T.PANEL, width=px(88))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(parent, bg=T.LINE, width=1).pack(side="left", fill="y")
        self.side = side
        self.nav_buttons = {}
        for key, icon, text in NAV:
            f = tk.Frame(side, bg=T.PANEL, cursor="hand2")
            f.pack(fill="x", pady=(px(2), 0))
            i = tk.Label(f, text=icon, bg=T.PANEL, fg=T.MUTE, font=T.font("h1"))
            i.pack(pady=(px(7), 0))
            t = tk.Label(f, text=text, bg=T.PANEL, fg=T.MUTE, font=T.font("tiny"))
            t.pack(pady=(0, px(7)))
            for w in (f, i, t):
                w.bind("<Button-1>", lambda e, k=key: self.switch_view(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self.nav_buttons[key] = (f, i, t)
        # accent bar that glides to the active item
        self.nav_indicator = tk.Frame(side, bg=T.ACC, width=px(3))
        self._indicator_y = 0.0
        self._indicator_h = 0.0
        bottom = tk.Frame(side, bg=T.PANEL)
        bottom.pack(side="bottom", fill="x", pady=px(10))
        for text, cmd, tiptext in (("⚙", self.show_settings, "Settings (Ctrl+,)"),
                                   ("?", self.show_shortcuts, "Keyboard shortcuts (?)")):
            b = tk.Label(bottom, text=text, bg=T.PANEL, fg=T.FAINT, font=T.font("h2"), cursor="hand2")
            b.pack(side="left", expand=True)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, b=b: b.configure(fg=T.TEXT))
            b.bind("<Leave>", lambda e, b=b: b.configure(fg=T.FAINT))
            T.tip(b, tiptext)

    def _nav_hover(self, key: str, on: bool) -> None:
        if key == self.current_view or (key == "cull" and self.current_view == "welcome"):
            return
        f, i, t = self.nav_buttons[key]
        start = f.cget("bg")
        end = T.SURF if on else T.PANEL

        def step(p):
            c = T.lerp_color(start, end, p)
            for w in (f, i, t):
                w.configure(bg=c)

        T.animate(f, 140, step, key=("nav", key))

    def _move_indicator(self, key: str) -> None:
        f = self.nav_buttons.get(key, (None,))[0]
        if f is None:
            self.nav_indicator.place_forget()
            return
        self.update_idletasks()
        y1, h1 = float(f.winfo_y()), float(f.winfo_height())
        y0, h0 = self._indicator_y, self._indicator_h
        if h0 == 0:
            y0, h0 = y1, h1

        def step(t):
            self._indicator_y, self._indicator_h = T.lerp(y0, y1, t), T.lerp(h0, h1, t)
            self.nav_indicator.place(x=0, y=int(self._indicator_y) + px(6), width=px(3),
                                     height=max(1, int(self._indicator_h) - px(12)))
            self.nav_indicator.lift()

        T.animate(self.nav_indicator, 260, step, key="nav-indicator")

    def switch_view(self, key: str) -> None:
        shown = "welcome" if (key in NEEDS_LIBRARY and self.lib is None) else key
        if key == self.current_view and self._shown_frame is self.views[shown]:
            return
        if self._shown_frame is not None:
            self._shown_frame.pack_forget()
        self.current_view = key
        for k, (f, i, t) in self.nav_buttons.items():
            on = k == key
            bg = T.SURF if on else T.PANEL
            for w in (f, i, t):
                w.configure(bg=bg)
            i.configure(fg=T.ACC if on else T.MUTE)
            t.configure(fg=T.TEXT if on else T.MUTE)
        self._move_indicator(key)
        v = self.views[shown]
        self._shown_frame = v
        v.pack(fill="both", expand=True)
        if hasattr(v, "on_show"):
            v.on_show()
        if shown == "cull":
            v.grid.canvas.focus_set()

    # ================================================================ folders
    def _recent_menu(self, e) -> None:
        m = tk.Menu(self, tearoff=0, bg=T.SURF, fg=T.TEXT, activebackground=T.ACC_DIM, activeforeground=T.TEXT)
        recent = [r for r in self.settings.get("recent", []) if r]
        for r in recent[:12]:
            m.add_command(label=r, command=lambda r=r: self.open_folder(Path(r)))
        if recent:
            m.add_separator()
            m.add_command(label="Clear list", command=lambda: self.settings.__setitem__("recent", []))
        if self.lib:
            m.add_separator()
            m.add_command(label="Show this folder in Explorer", command=lambda: fo.open_in_file_manager(self.lib.root))
            m.add_command(label="Re-analyse everything (clear cache)", command=lambda: self.start_analysis(force=True))
        if not recent and not self.lib:
            m.add_command(label="No recent folders", state="disabled")
        m.tk_popup(e.x_root, e.y_root + px(10))

    def open_folder(self, folder: Optional[Path] = None) -> None:
        if folder is None:
            d = filedialog.askdirectory(title="Open a folder of photos", parent=self,
                                        initialdir=self.settings.get("recent", [None])[0] if self.settings.get("recent") else None)
            if not d:
                return
            folder = Path(d)
        folder = Path(folder)
        if not folder.is_dir():
            self.toast(f"Folder not found: {folder}", "error")
            return
        if self.busy:
            self.stop_task()
            self.after(300, lambda: self.open_folder(folder))
            return
        if self.lib is not None:
            self.lib.close()
        rec = [str(folder)] + [r for r in self.settings.get("recent", []) if r != str(folder)]
        self.settings["recent"] = rec[:15]
        recursive = self.recursive_var.get()

        def work(progress, stop):
            lib = Library(folder, recursive=recursive)
            lib.scan()
            return lib

        def done(lib: Library):
            self._set_library(lib)
            pending = len(lib.pending_paths())
            if pending and self.settings.get("auto_analyze", True):
                self.start_analysis()
            elif pending:
                self.toast(f"Opened {folder.name}: {len(lib.photos)} photos. Press Analyse when you're ready.", "info")
            else:
                self.toast(f"Opened {folder.name}: {len(lib.photos)} photos (all analysed already).", "ok")

        self.folder_btn.configure(text=f"{folder.name}  ▾", fg=T.TEXT)
        self.run_task(f"Reading {folder.name}…", work, done, indeterminate=True)

    def _set_library(self, lib: Library) -> None:
        self.lib = lib
        self.title(f"{lib.root.name} — Photography Tools")
        self.folder_btn.configure(text=f"{lib.root.name}  ▾", fg=T.TEXT)
        self.images.set_library(lib)
        self.undo_stack.clear()
        cull = self.views["cull"]
        cull.grid.current = None
        cull.grid.selected = {}
        if cull.mode != "grid":
            cull.close_viewer()
        cull.apply_filters(keep_scroll=False)
        if self._shown_frame is self.views["welcome"] or self.current_view == "import":
            target = self.current_view if self.current_view in NEEDS_LIBRARY else "cull"
            self.current_view = ""
            self.switch_view(target)
        elif hasattr(self._shown_frame, "on_show") and self.current_view != "cull":
            self._shown_frame.on_show()
        self.refresh_counts()
        self._update_analyse_hint()

    def reload_library(self, keep_view: bool = True, recursive: Optional[bool] = None) -> None:
        if not self.lib:
            return
        root = self.lib.root
        if recursive is None:
            recursive = self.lib.recursive
        else:
            self.recursive_var.set(recursive)
            self.settings["recursive"] = recursive
        self.lib.close()

        def work(progress, stop):
            lib = Library(root, recursive=recursive)
            lib.scan()
            return lib

        def done(lib):
            self._set_library(lib)
            if lib.pending_paths():
                self.start_analysis()

        self.run_task("Refreshing…", work, done, indeterminate=True)

    def set_recursive(self, on: bool, then: Optional[Callable] = None) -> None:
        self.recursive_var.set(on)
        self.settings["recursive"] = on
        if self.lib and self.lib.recursive != on:
            root = self.lib.root
            self.open_folder(root)
            if then:
                self.after(800, lambda: self._when_idle(then))

    def _when_idle(self, fn) -> None:
        if self.busy:
            self.after(300, lambda: self._when_idle(fn))
        else:
            fn()

    # ================================================================ tasks
    @property
    def busy(self) -> bool:
        return self._task_thread is not None and self._task_thread.is_alive()

    def run_task(self, label: str, work: Callable, on_done: Callable, on_error: Optional[Callable] = None,
                 indeterminate: bool = False) -> None:
        if self.busy:
            self.toast("Please wait for the current task to finish (or press Stop).", "warn")
            return
        self._task_stop = False
        self.analyse_btn.pack_forget()
        self.progress.pack(side="left")
        self.stop_btn.pack(side="left", padx=(px(8), 0))
        self.task_label.pack_forget()
        self.task_label.pack(side="left", padx=px(10))
        self.task_label.configure(text=label, fg=T.TEXT)
        if indeterminate:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.configure(mode="determinate", value=0, maximum=100)
        t0 = time.monotonic()

        def progress(done, total, name=""):
            self._task_q.put(("progress", done, total, name, t0))

        def target():
            try:
                res = work(progress, lambda: self._task_stop)
                self._task_q.put(("done", on_done, res))
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                self._task_q.put(("error", on_error, e, tb))

        self._task_label_base = label
        self._task_thread = threading.Thread(target=target, daemon=True)
        self._task_thread.start()

    def stop_task(self) -> None:
        self._task_stop = True
        self.task_label.configure(text="Stopping…")

    def _end_task_ui(self) -> None:
        self.images.defer_uncached = False
        self.progress.stop()
        self.progress.pack_forget()
        self.stop_btn.pack_forget()
        self.task_label.pack_forget()
        self.analyse_btn.pack(side="left")
        self.task_label.pack(side="left", padx=px(10))
        self.task_label.configure(text="", fg=T.MUTE)
        self._update_analyse_hint()

    def _poll_task(self) -> None:
        try:
            while True:
                msg = self._task_q.get_nowait()
                if msg[0] == "progress":
                    _, done, total, name, t0 = msg
                    if total:
                        if str(self.progress.cget("mode")) != "determinate" or float(self.progress.cget("maximum")) != total:
                            self.progress.stop()
                            self.progress.configure(mode="determinate", maximum=total, value=0)
                        self._progress_target = done
                        self._smooth_progress()
                        el = time.monotonic() - t0
                        eta = ""
                        if done > 3 and done < total:
                            rem = el / done * (total - done)
                            eta = f" · about {int(rem // 60)}m {int(rem % 60)}s left" if rem >= 60 else f" · {int(rem)}s left"
                        self.task_label.configure(text=f"{self._task_label_base} {done}/{total}{eta}")
                elif msg[0] == "done":
                    _, cb, res = msg
                    self._end_task_ui()
                    try:
                        cb(res)
                    except Exception:
                        self._report_exception(*sys.exc_info())
                elif msg[0] == "error":
                    _, cb, e, tb = msg
                    self._end_task_ui()
                    if cb:
                        cb(e)
                    else:
                        self._show_error(e, tb)
        except queue.Empty:
            pass
        if self._analysis_updates and time.monotonic() - self._last_live_refresh > 0.6:
            keys = self._analysis_updates
            self._analysis_updates = set()
            self._last_live_refresh = time.monotonic()
            for k in keys:
                self.images.invalidate(k)
            if self.current_view == "cull":
                self.views["cull"].grid.redraw()
            self.refresh_counts()
        self.after(60, self._poll_task)

    def _smooth_progress(self) -> None:
        """Glide the bar towards the latest value instead of jumping."""
        if getattr(self, "_progress_job", None):
            return

        def tick():
            self._progress_job = None
            try:
                cur = float(self.progress.cget("value"))
            except tk.TclError:
                return
            target = getattr(self, "_progress_target", cur)
            nxt = cur + (target - cur) * 0.35
            if abs(target - nxt) < 0.05:
                nxt = target
            self.progress.configure(value=nxt)
            if nxt != target:
                self._progress_job = self.after(16, tick)

        self._progress_job = self.after(16, tick)

    def start_analysis(self, force: bool = False) -> None:
        lib = self.lib
        if lib is None:
            self.open_folder()
            return
        if force and not messagebox.askyesno("Re-analyse", "Clear the cache and analyse every photo again?", parent=self):
            return
        pending = len(lib.photos) if force else len(lib.pending_paths()) + sum(1 for p in lib.photos if p.error)
        if not pending:
            self.toast("Everything is already analysed.", "info")
            return

        from ..core import analysis as _an

        threads = int(self.settings.get("threads", 0) or 0)
        _an.MAX_WORKERS = threads if threads > 0 else _an.DEFAULT_WORKERS

        def work(progress, stop):
            return lib.analyze(progress, stop, on_photo=lambda p: self._analysis_updates.add(p.key), force=force)

        self.images.defer_uncached = True

        def done(run):
            self.images.defer_uncached = False
            if self.lib is not lib:
                return
            cull = self.views["cull"]
            if run.done - run.cached > len(lib.photos) // 2:  # first analysis: start at the top in capture order
                cull.grid.current = None
                cull.grid.selected = {}
                cull.apply_filters(keep_scroll=False)
            else:
                cull.apply_filters()
            self.refresh_all()
            msg = f"Analysed {run.done} photos in {run.elapsed:.0f}s."
            if run.stopped:
                msg = f"Stopped after {run.done} of {run.total} photos. Press Analyse to continue."
            if run.errors:
                msg += f" {len(run.errors)} couldn't be read."
            self.toast(msg, "warn" if run.errors or run.stopped else "ok")

        self.run_task("Analysing", work, done)

    def _update_analyse_hint(self) -> None:
        lib = self.lib
        if lib is None:
            self.analyse_btn.pack_forget()
            self.task_label.configure(text="")
            return
        if not self.busy and not self.analyse_btn.winfo_ismapped():
            self.analyse_btn.pack(side="left", before=self.task_label)
        n = len(lib.pending_paths())
        if n:
            self.analyse_btn.configure(text=f"Analyse {n} photos", style="Accent.TButton")
            self.task_label.configure(text="")
        else:
            self.analyse_btn.configure(text="✓ Analysed", style="TButton")
            errs = sum(1 for p in lib.photos if p.error)
            self.task_label.configure(text=f"{errs} files couldn't be read" if errs else "",
                                      fg=T.WARN if errs else T.MUTE)

    # ================================================================ refresh helpers
    def refresh_counts(self) -> None:
        c = self.lib.counts() if self.lib else None
        for flt, key, word in (("all", "total", "photos"), ("picks", "picks", "picks"),
                               ("rejects", "rejects", "rejects"), ("unflagged", "unflagged", "to review")):
            self.count_labels[flt].configure(text=f"{c[key]} {word}" if c else "")

    def show_flag_filter(self, flag: str) -> None:
        """Jump to Cull showing only picks / rejects / unflagged (from the counts in the top bar)."""
        if not self.lib:
            return
        cull = self.views["cull"]
        self.switch_view("cull")
        if cull.mode != "grid":
            cull.close_viewer()
        cull.flag_var.set(flag)
        cull.apply_filters(keep_scroll=False)

    def refresh_all(self) -> None:
        self.refresh_counts()
        v = self.views.get(self.current_view)
        if self.current_view == "cull":
            v.refresh()
        elif self.current_view == "groups":
            v.populate()
        elif hasattr(v, "on_show"):
            v.on_show()

    def toast(self, msg: str, kind: str = "info", action=None) -> None:
        T.Toast(self, msg, kind, action=action)

    def toast_undo(self, msg: str) -> None:
        self.toast(msg, "ok", action=("Undo", self.undo_decision))

    # ================================================================ decisions
    def _targets(self) -> List[Photo]:
        v = self.views.get(self.current_view)
        if v is not None and hasattr(v, "selection"):
            return v.selection()
        return []

    def save_selected_as_jpg(self, photos: Optional[List[Photo]] = None) -> None:
        """Convert the selected photos to full-size JPEGs in a folder you choose (originals untouched)."""
        if not self.lib:
            return
        photos = list(photos if photos is not None else self._targets())
        if not photos:
            return self.toast("Select some photos first.", "warn")
        lib = self.lib
        start = self.settings.get("jpg_dest") or str(lib.root.parent)
        chosen = filedialog.askdirectory(title=f"Save {len(photos)} photo{'s' if len(photos) != 1 else ''} as JPG to…",
                                         initialdir=start, parent=self)
        if not chosen:
            return
        self.settings["jpg_dest"] = chosen
        T.save_settings(self.settings)
        from ..core import web_export as we

        dest = Path(chosen) / f"{lib.root.name} - JPG"

        def work(progress, stop):
            return we.save_as_jpg(lib, photos, dest, 95, progress, stop)

        def done(res):
            n, _journal, errors = res
            self.toast(f"Saved {n} JPG{'s' if n != 1 else ''} to {dest.name}."
                       + (f" {len(errors)} failed." if errors else ""), "warn" if errors else "ok")
            if n:
                fo.open_in_file_manager(dest)

        self.run_task("Saving JPGs", work, done)

    def push_undo(self, photos: List[Photo]) -> None:
        snap = [(p, p.rating, p.flag, p.label, p.touched) for p in photos]
        self.undo_stack.append(snap)
        del self.undo_stack[:-60]

    def undo_decision(self) -> None:
        if not self.undo_stack or not self.lib:
            self.toast("Nothing to undo.", "info")
            return
        snap = self.undo_stack.pop()
        for p, r, f, l, t in snap:
            p.rating, p.flag, p.label, p.touched = r, f, l, t
        self.lib.mark_dirty()
        self.refresh_all()
        self.toast(f"Undid change to {len(snap)} photo{'s' if len(snap) != 1 else ''}.", "info")

    def _after_decision(self, targets: List[Photo]) -> None:
        self.refresh_counts()
        view = self.views.get(self.current_view)
        if self.current_view == "cull":
            cull = view
            if self.auto_advance.get() and len(targets) == 1 and cull.mode in ("grid", "loupe"):
                nxt = cull.grid.next_after(targets[0])
                if nxt is not None:
                    cull.grid.select(nxt)
            cull.refresh([p.key for p in targets])
        elif self.current_view == "groups":
            if self.auto_advance.get() and len(targets) == 1:
                nxt = view.grid.next_after(targets[0])
                if nxt is not None:
                    view.grid.select(nxt)
            view.refresh()

    def _stamp(self, targets: List[Photo], kind: str) -> None:
        """Brief visual confirmation on the photo(s) just decided."""
        view = self.views.get(self.current_view)
        if self.current_view == "cull" and view.mode != "grid":
            pane = view.loupe.pane if view.mode == "loupe" else view.compare.panes[view.compare.active]
            pane.flash(kind)
        elif self.current_view in ("cull", "groups"):
            for p in targets[:30]:
                view.grid.stamp(p, kind)

    def decide_flag(self, flag: Optional[str]) -> None:
        targets = self._targets()
        if not targets or not self.lib:
            return
        self.push_undo(targets)
        self.lib.set_flag(targets, flag)
        self._stamp(targets, {"pick": "pick", "reject": "reject"}.get(flag or "", "clear"))
        self._after_decision(targets)

    def decide_rating(self, n: int) -> None:
        targets = self._targets()
        if not targets or not self.lib:
            return
        self.push_undo(targets)
        self.lib.set_rating(targets, n)
        self._stamp(targets, f"star{n}")
        self._after_decision(targets)

    def decide_label(self, label: Optional[str]) -> None:
        targets = self._targets()
        if not targets or not self.lib:
            return
        self.push_undo(targets)
        self.lib.set_label(targets, label)
        self.refresh_counts()
        self.views[self.current_view].refresh() if hasattr(self.views[self.current_view], "refresh") else None

    def keep_best_of(self, photos: List[Photo]) -> None:
        if len(photos) < 2 or not self.lib:
            return
        analysed = [p for p in photos if p.quality]
        if not analysed:
            return
        max_sharp = max(p.composite_sharpness for p in analysed) or 1
        best = max(analysed, key=lambda p: 0.55 * p.composite_sharpness / max_sharp * 100 + 0.3 * p.exposure_score
                   + 0.15 * p.quality_score)
        self.push_undo(photos)
        self.lib.set_flag([best], FLAG_PICK)
        self.lib.set_flag([p for p in photos if p is not best], FLAG_REJECT)
        self.refresh_all()
        self.toast_undo(f"Picked {best.name}, rejected {len(photos) - 1} others.")

    def make_best(self, p: Photo) -> None:
        if self.lib:
            self.lib.set_group_best(p.key)
            self.refresh_all()

    def remove_from_group(self, p: Photo) -> None:
        if self.lib:
            self.lib.remove_from_group(p.key)
            self.refresh_all()

    def show_group(self, gid: int) -> None:
        self.switch_view("groups")
        self.views["groups"].populate(focus_gid=gid)

    # ================================================================ viewer
    def open_viewer(self, photo: Photo) -> None:
        if self.current_view != "cull":
            self.switch_view("cull")
        self.views["cull"].open_loupe(photo)

    def open_compare(self, photos: List[Photo]) -> None:
        if self.current_view != "cull":
            self.switch_view("cull")
        self.views["cull"].open_compare(photos)

    def close_viewer(self) -> None:
        self.views["cull"].close_viewer()

    def on_viewer_active_changed(self, photo: Photo) -> None:
        self.views["cull"].inspector.show(photo, [photo])

    def open_external(self, p: Photo) -> None:
        fo.open_with_default_app(p.display)

    def reveal(self, p: Photo) -> None:
        fo.open_in_file_manager(p.master)

    # ================================================================ dialogs
    def show_settings(self) -> None:
        from .dialogs import SettingsDialog

        SettingsDialog(self)

    def show_about(self) -> None:
        from .dialogs import AboutDialog

        AboutDialog(self)

    def toggle_fullscreen(self) -> None:
        try:
            self.attributes("-fullscreen", not bool(self.attributes("-fullscreen")))
        except tk.TclError:
            pass

    def _set_icon(self) -> None:
        assets = Path(__file__).resolve().parent / "assets"
        try:
            self._icon_img = tk.PhotoImage(file=str(assets / "icon.png"))
            self.iconphoto(True, self._icon_img)
        except Exception:
            self._icon_img = None
        if sys.platform == "win32":
            try:
                import ctypes

                # Own taskbar group + icon instead of python.exe's.
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Demi.PhotographyTools")
                self.iconbitmap(default=str(assets / "icon.ico"))
            except Exception:
                pass

    def show_shortcuts(self) -> None:
        keys.show_shortcuts(self)

    def _show_error(self, e: Exception, tb: str = "") -> None:
        log = Path.home() / ".photo_sorter" / "error.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(time.strftime("\n[%Y-%m-%d %H:%M:%S]\n") + (tb or repr(e)))
        except OSError:
            pass
        messagebox.showerror("Something went wrong", f"{e}\n\nDetails were saved to:\n{log}", parent=self)

    def _report_exception(self, exc, val, tb) -> None:
        self._show_error(val, "".join(traceback.format_exception(exc, val, tb)))

    # ================================================================ drag and drop / lifecycle
    def _setup_drop(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import windnd  # type: ignore

            def dropped(files):
                if not files:
                    return
                f = files[0]
                if isinstance(f, bytes):
                    try:
                        f = f.decode("utf-8")
                    except UnicodeDecodeError:
                        f = f.decode("mbcs", errors="ignore")
                p = Path(f)
                target = p if p.is_dir() else p.parent
                self.after(10, lambda: self.open_folder(target))

            windnd.hook_dropfiles(self, func=dropped)
        except Exception:
            pass

    def _autosave(self) -> None:
        if self.lib is not None and self.lib.dirty:
            self.lib.save_state()
        self.after(5000, self._autosave)

    def on_close(self) -> None:
        self._task_stop = True
        try:
            zoomed = self.state() == "zoomed"
        except tk.TclError:
            zoomed = False
        self.settings["zoomed"] = zoomed
        if not zoomed:
            self.settings["geometry"] = self.geometry()
        self.settings["recursive"] = self.recursive_var.get()
        T.save_settings(self.settings)
        if self.lib is not None:
            try:
                self.lib.close()
            except Exception:
                pass
        self.images.shutdown()
        self.destroy()


def main(folder: Optional[str] = None, view: str = "cull") -> None:
    app = App(Path(folder) if folder else None, start_view=view)
    app.mainloop()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
