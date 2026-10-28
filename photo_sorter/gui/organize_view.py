"""Organise screen: folder and rename templates, previewed before anything moves."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from ..core import export as ex
from ..core import organizer
from ..core.library import Photo
from . import theme as T
from .common import header
from .theme import px

SCOPES = {"all": "All photos", "picks": "Picks only", "not_rejected": "Everything except rejects",
          "rejects": "Rejects only", "visible": "Photos shown in Cull (current filter)"}


class OrganizeView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        header(self, "Organise", "Sort photos into folders and rename them from templates. You'll see every "
                                  "change before anything moves, RAW+JPEG pairs and sidecars stay together, "
                                  "nothing is ever overwritten, and every run can be undone from History.")
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=px(24), pady=(0, px(18)))
        body.columnconfigure(0, weight=0, minsize=px(430))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        lsf = T.ScrollFrame(body)
        lsf.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        left = lsf.inner
        right = tk.Frame(body, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")

        c = T.Card(left, "Folders")
        c.pack(fill="x", pady=(0, px(10)), padx=(0, px(4)))
        self.folder_preset = tk.StringVar(value="Year / Date")
        cb = ttk.Combobox(c.body, textvariable=self.folder_preset, values=list(organizer.FOLDER_PRESETS.keys()),
                          state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda e: self.folder_tmpl.set(organizer.FOLDER_PRESETS[self.folder_preset.get()]))
        self.folder_tmpl = tk.StringVar(value=organizer.FOLDER_PRESETS["Year / Date"])
        ttk.Entry(c.body, textvariable=self.folder_tmpl).pack(fill="x", pady=(px(6), 0))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(6), 0))
        tk.Label(r, text="New event after a gap of", bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(side="left")
        self.gap_var = tk.IntVar(value=45)
        ttk.Spinbox(r, from_=5, to=1440, increment=5, width=5, textvariable=self.gap_var).pack(side="left", padx=px(6))
        tk.Label(r, text="minutes  (for {event})", bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(side="left")

        c = T.Card(left, "File names")
        c.pack(fill="x", pady=(0, px(10)), padx=(0, px(4)))
        self.rename_preset = tk.StringVar(value="Keep original names")
        cb = ttk.Combobox(c.body, textvariable=self.rename_preset, values=list(organizer.RENAME_PRESETS.keys()),
                          state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda e: self.rename_tmpl.set(organizer.RENAME_PRESETS[self.rename_preset.get()]))
        self.rename_tmpl = tk.StringVar(value="")
        ttk.Entry(c.body, textvariable=self.rename_tmpl).pack(fill="x", pady=(px(6), 0))
        tok = "\n".join(f"{a}  —  {b}" for a, b in organizer.TOKENS_HELP)
        tl = tk.Label(c.body, text=tok, bg=T.SURF, fg=T.FAINT, font=T.font("tiny"), justify="left", anchor="w")
        tl.pack(fill="x", pady=(px(8), 0))
        T.bind_wrap(c, tl, px(36))

        c = T.Card(left, "Which photos, and where")
        c.pack(fill="x", pady=(0, px(10)), padx=(0, px(4)))
        self.scope_cb = ttk.Combobox(c.body, state="readonly", values=list(SCOPES.values()))
        self.scope_cb.set(SCOPES["all"])
        self.scope_cb.pack(fill="x")
        self.mode_var = tk.StringVar(value="move")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        ttk.Radiobutton(r, text="Move", value="move", variable=self.mode_var, style="Card.TRadiobutton").pack(side="left")
        ttk.Radiobutton(r, text="Copy", value="copy", variable=self.mode_var, style="Card.TRadiobutton").pack(side="left", padx=px(12))
        self.dest_var = tk.StringVar(value="")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        tk.Label(r, text="Into:", bg=T.SURF, fg=T.MUTE).pack(side="left")
        self.dest_label = tk.Label(r, text="this folder", bg=T.SURF, fg=T.TEXT, anchor="w")
        self.dest_label.pack(side="left", fill="x", expand=True, padx=px(6))
        ttk.Button(r, text="Change…", style="Small.TButton", command=self.pick_dest).pack(side="right")
        ttk.Button(r, text="Reset", style="Small.TButton", command=lambda: self._set_dest("")).pack(side="right", padx=px(4))
        self.cleanup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(c.body, text="Remove folders left empty", variable=self.cleanup_var,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(px(6), 0))

        r = tk.Frame(left, bg=T.BG)
        r.pack(fill="x", pady=(px(4), px(10)))
        ttk.Button(r, text="Preview changes", style="Accent.TButton", command=self.preview).pack(side="left")
        self.apply_btn = ttk.Button(r, text="Apply", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=px(8))

        c = T.Card(left, "Flatten subfolders",
                   "Move every photo from subfolders back into the main folder - handy for undoing an old sort "
                   "or merging card dumps. Same-name files get ' (1)' added instead of being overwritten.")
        c.pack(fill="x", padx=(0, px(4)))
        ttk.Button(c.body, text="Preview flatten", command=self.preview_flatten).pack(anchor="w")

        # --- preview pane
        self.summary = tk.Label(right, text="Choose a layout and press Preview changes.", bg=T.BG, fg=T.MUTE,
                                anchor="w", justify="left")
        self.summary.pack(fill="x", pady=(0, px(6)))
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        f1 = tk.Frame(nb, bg=T.SURF)
        f2 = tk.Frame(nb, bg=T.SURF)
        nb.add(f1, text="Folders")
        nb.add(f2, text="Every file")
        self.folders_tree = ttk.Treeview(f1, columns=("n",), show="tree headings")
        self.folders_tree.heading("#0", text="Destination folder")
        self.folders_tree.heading("n", text="Photos")
        self.folders_tree.column("n", width=px(80), anchor="e", stretch=False)
        self.folders_tree.pack(fill="both", expand=True)
        self.files_tree = ttk.Treeview(f2, columns=("from", "to"), show="headings")
        self.files_tree.heading("from", text="From")
        self.files_tree.heading("to", text="To")
        vs = ttk.Scrollbar(f2, orient="vertical", command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.files_tree.pack(fill="both", expand=True)
        self.plan: Optional[organizer.OrganizePlan] = None
        self._flatten = False
        # A previewed plan only holds for the settings it was made with; changing any of them
        # means Apply would do something other than what's on screen.
        for var in (self.folder_tmpl, self.rename_tmpl, self.mode_var, self.gap_var):
            var.trace_add("write", lambda *a: self._settings_changed())
        self.scope_cb.bind("<<ComboboxSelected>>", lambda e: self._settings_changed(), add="+")

    def on_show(self) -> None:
        pass

    def _set_dest(self, path: str) -> None:
        self.dest_var.set(path)
        self.dest_label.configure(text=path or "this folder")
        self._invalidate()

    def pick_dest(self) -> None:
        d = filedialog.askdirectory(title="Organise into…", parent=self)
        if d:
            self._set_dest(d)

    def _invalidate(self) -> None:
        self.plan = None
        self.apply_btn.configure(state="disabled", style="TButton")

    def _settings_changed(self) -> None:
        if self.plan is not None:
            self._invalidate()
            self.summary.configure(text="Settings changed. Press Preview changes to see the new result.", fg=T.MUTE)

    def _scope(self) -> List[Photo]:
        lib = self.app.lib
        key = next((k for k, v in SCOPES.items() if v == self.scope_cb.get()), "all")
        if key == "visible":
            return list(self.app.views["cull"].visible)
        return ex.select(lib, key if key != "all" else "all")

    def preview(self) -> None:
        lib = self.app.lib
        if not lib:
            return self.app.toast("Open a folder first.", "warn")
        bad = organizer.validate_template(self.folder_tmpl.get() + self.rename_tmpl.get())
        if bad:
            return self.app.toast("Unknown token(s): " + ", ".join("{" + b + "}" for b in bad), "error")
        try:
            gap = float(self.gap_var.get())
        except (tk.TclError, ValueError):
            gap = 45.0
        photos = self._scope()
        self.plan = organizer.plan_organize(lib, photos, self.folder_tmpl.get(), self.rename_tmpl.get(),
                                            Path(self.dest_var.get()) if self.dest_var.get() else None,
                                            self.mode_var.get(), gap)
        self._flatten = False
        self._show_plan(len(photos))

    def preview_flatten(self) -> None:
        lib = self.app.lib
        if not lib:
            return self.app.toast("Open a folder first.", "warn")
        if not lib.recursive:
            if messagebox.askyesno("Include subfolders?", "Flattening needs the photos in subfolders.\n\n"
                                   "Reload this folder with 'Include subfolders' turned on?", parent=self):
                self.app.set_recursive(True, then=self.preview_flatten)
            return
        self.plan = organizer.plan_flatten(lib)
        self._flatten = True
        self._show_plan(sum(1 for p in lib.photos if p.rel_dir))

    def _show_plan(self, n_scope: int) -> None:
        plan = self.plan
        self.folders_tree.delete(*self.folders_tree.get_children())
        self.files_tree.delete(*self.files_tree.get_children())
        nodes = {}
        for folder, n in plan.folders().items():
            parent = ""
            parts = [] if folder == "." else Path(folder).parts
            acc = ""
            for part in parts[:-1]:
                acc = os.path.join(acc, part)
                if acc not in nodes:
                    nodes[acc] = self.folders_tree.insert(parent, "end", text=part, open=True)
                parent = nodes[acc]
            self.folders_tree.insert(parent, "end", text=parts[-1] if parts else "(main folder)", values=(n,))
        root = self.app.lib.root
        for item in plan.items[:3000]:
            for src, dst in item.moves:
                self.files_tree.insert("", "end", values=(_rel(src, root), _rel(dst, plan.target_root)))
        verb = "copied" if plan.mode == "copy" else "moved"
        txt = f"{len(plan.items)} photos ({plan.file_count} files) will be {verb}"
        if plan.unchanged:
            txt += f" · {plan.unchanged} already in place"
        if plan.collisions:
            txt += f" · {plan.collisions} name clashes will get ' (1)' suffixes"
        if len(plan.items) > 3000:
            txt += " · showing the first 3000"
        self.summary.configure(text=txt, fg=T.TEXT)
        self.apply_btn.configure(state="normal" if plan.items else "disabled",
                                 style="Accent.TButton" if plan.items else "TButton")

    def apply(self) -> None:
        lib, plan = self.app.lib, self.plan
        if not lib or not plan or not plan.items:
            return
        verb = "Copy" if plan.mode == "copy" else "Move"
        if not messagebox.askyesno("Apply", f"{verb} {len(plan.items)} photos now?\n\nYou can undo this from History.",
                                   parent=self):
            return
        cleanup = self.cleanup_var.get() or self._flatten
        label = "Flatten subfolders" if self._flatten else f"Organise: {self.folder_tmpl.get() or 'rename'}"

        def work(progress, stop):
            return organizer.apply_plan(lib, plan, progress, stop, label=label, remove_empty_folders=cleanup)

        def done(res):
            journal, errors = res
            lib.migrate_keys(journal)
            self._invalidate()
            into_subfolders = (plan.mode == "move" and plan.target_root == lib.root
                               and any(m[1].parent != lib.root for it in plan.items for m in it.moves))
            # Photos now live in subfolders: switch on "Include subfolders" so they stay visible.
            self.app.reload_library(keep_view=True, recursive=True if into_subfolders else None)
            msg = f"{journal.done_count} files {'copied' if plan.mode == 'copy' else 'moved'}."
            if errors:
                msg += f" {len(errors)} problems - see History."
            self.app.toast(msg, "warn" if errors else "ok")

        self.app.run_task(f"{verb.rstrip('e')}ing photos…", work, done)


def _rel(p: Path, root: Path) -> str:
    try:
        return str(Path(p).relative_to(root))
    except ValueError:
        return str(p)
