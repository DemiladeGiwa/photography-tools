"""History screen: every journaled file operation, each one undoable."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import file_operations as fo
from . import theme as T
from .common import header
from .theme import px

class HistoryView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        header(self, "History", "Every move, copy, export and reject run is recorded here and can be undone.")
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=px(24), pady=(0, px(18)))
        bar = tk.Frame(body, bg=T.BG)
        bar.pack(fill="x", pady=(0, px(8)))
        ttk.Button(bar, text="Undo selected", style="Accent.TButton", command=self.undo).pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.on_show).pack(side="left", padx=px(8))
        cols = ("when", "what", "files", "status")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("when", "When", 150), ("what", "What", 420), ("files", "Files", 70), ("status", "Status", 160)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=px(w), anchor="w" if c != "files" else "e", stretch=c == "what")
        self.tree.pack(fill="both", expand=True)
        self.detail = tk.Label(body, text="", bg=T.BG, fg=T.MUTE, font=T.font("small"), anchor="w", justify="left")
        self.detail.pack(fill="x", pady=(px(6), 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._detail())
        self.paths = {}

    def on_show(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.paths = {}
        lib = self.app.lib
        if not lib:
            return
        for path in fo.list_journals(lib.root)[:300]:
            try:
                j = fo.Journal.load(path)
            except Exception:
                continue
            undone = sum(1 for e in j.entries if e.status == "undone")
            failed = sum(1 for e in j.entries if e.status == "failed")
            status = "Undone" if undone and not j.done_count else ("Partly undone" if undone else "Done")
            if failed:
                status += f" · {failed} failed"
            label = j.label or j.kind.title()
            iid = self.tree.insert("", "end", values=(time.strftime("%Y-%m-%d %H:%M", time.localtime(j.started_at)),
                                                      label, len(j.entries), status))
            self.paths[iid] = path

    def _detail(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        j = fo.Journal.load(self.paths[sel[0]])
        failed = [e for e in j.entries if e.status == "failed"]
        txt = f"{j.done_count} done, {len(failed)} failed."
        if failed:
            txt += "  First problem: " + (failed[0].error or "")
        self.detail.configure(text=txt)

    def undo(self) -> None:
        lib = self.app.lib
        sel = self.tree.selection()
        if not lib or not sel:
            return
        j = fo.Journal.load(self.paths[sel[0]])
        if not j.undoable:
            return self.app.toast("Nothing left to undo in that entry.", "info")
        if not messagebox.askyesno("Undo", f"Undo '{j.label or j.kind}'?", parent=self):
            return

        def work(progress, stop):
            return fo.undo_journal(j, lib.root), j

        def done(res):
            (restored, errors), journal = res
            lib.migrate_keys(journal, reverse=True)
            self.app.reload_library(keep_view=True)
            self.on_show()
            self.app.toast(f"Restored {restored} files." + (f" {len(errors)} couldn't be restored." if errors else ""),
                           "warn" if errors else "ok")

        self.app.run_task("Undoing…", work, done)
