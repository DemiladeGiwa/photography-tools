"""Import from a memory card (or any folder) into the photo library, sorted by date."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from ..core import importer, organizer
from . import theme as T
from .theme import px

DEFAULT_TEMPLATE = "{YYYY}/{YYYY}-{MM}-{DD}"


class ImportView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=px(24), pady=(px(18), px(8)))
        tk.Label(head, text="Import", bg=T.BG, fg=T.TEXT, font=T.font("h1"), anchor="w").pack(fill="x")
        tk.Label(head, text="Copy photos off a memory card into your library, sorted into dated folders. "
                            "Anything you've imported before is skipped, and nothing on the card is changed.",
                 bg=T.BG, fg=T.MUTE, anchor="w", justify="left").pack(fill="x")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=px(24), pady=(0, px(18)))
        body.columnconfigure(0, weight=0, minsize=px(440))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=T.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        right = tk.Frame(body, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")

        c = T.Card(left, "1.  From", "Memory cards are found automatically when they're plugged in.")
        c.pack(fill="x", pady=(0, px(10)))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        self.source_var = tk.StringVar()
        self.source_cb = ttk.Combobox(r, textvariable=self.source_var)
        self.source_cb.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="…", width=3, command=self.pick_source).pack(side="left", padx=(px(4), 0))
        ttk.Button(r, text="⟳", width=3, command=self.detect).pack(side="left", padx=(px(4), 0))
        self.source_hint = tk.Label(c.body, text="", bg=T.SURF, fg=T.MUTE, font=T.font("small"), anchor="w")
        self.source_hint.pack(fill="x", pady=(px(4), 0))

        c = T.Card(left, "2.  Into", "Your main photo library folder.")
        c.pack(fill="x", pady=(0, px(10)))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        default_dest = app.settings.get("import_dest") or str(Path.home() / "Pictures")
        self.dest_var = tk.StringVar(value=default_dest)
        ttk.Entry(r, textvariable=self.dest_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="…", width=3, command=self.pick_dest).pack(side="left", padx=(px(4), 0))
        tk.Label(c.body, text="Folders", bg=T.SURF, fg=T.MUTE, font=T.font("small"), anchor="w").pack(
            fill="x", pady=(px(8), px(2)))
        self.tmpl_var = tk.StringVar(value=app.settings.get("import_template", DEFAULT_TEMPLATE))
        cb = ttk.Combobox(c.body, textvariable=self.tmpl_var,
                          values=[v for v in organizer.FOLDER_PRESETS.values() if "{flag}" not in v
                                  and "{rating}" not in v and "{label}" not in v and "{group}" not in v])
        cb.pack(fill="x")
        tk.Label(c.body, text="Rename (optional)", bg=T.SURF, fg=T.MUTE, font=T.font("small"), anchor="w").pack(
            fill="x", pady=(px(8), px(2)))
        self.rename_var = tk.StringVar(value="")
        ttk.Combobox(c.body, textvariable=self.rename_var,
                     values=[v for v in organizer.RENAME_PRESETS.values() if v]).pack(fill="x")
        self.videos_var = tk.BooleanVar(value=True)
        self.skip_var = tk.BooleanVar(value=True)
        self.open_var = tk.BooleanVar(value=True)
        for var, text in ((self.videos_var, "Include videos"), (self.skip_var, "Skip photos already imported"),
                          (self.open_var, "Open the photos when done")):
            ttk.Checkbutton(c.body, text=text, variable=var, style="Card.TCheckbutton").pack(anchor="w")

        r = tk.Frame(left, bg=T.BG)
        r.pack(fill="x", pady=(px(4), 0))
        ttk.Button(r, text="Check card", command=self.scan).pack(side="left")
        self.import_btn = ttk.Button(r, text="Import", style="Accent.TButton", command=self.run_import,
                                     state="disabled")
        self.import_btn.pack(side="left", padx=px(8))

        # --- preview
        self.summary = tk.Frame(right, bg=T.BG)
        self.summary.pack(fill="x", pady=(0, px(8)))
        self.stat_new = self._stat(self.summary, "new photos", T.ACC)
        self.stat_dup = self._stat(self.summary, "already imported", T.MUTE)
        self.stat_size = self._stat(self.summary, "to copy", T.TEXT)
        self.tree = ttk.Treeview(right, columns=("n",), show="tree headings")
        self.tree.heading("#0", text="Will be copied into")
        self.tree.heading("n", text="Photos")
        self.tree.column("n", width=px(80), anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.plan: Optional[importer.ImportPlan] = None

    def _stat(self, parent, label, color):
        f = tk.Frame(parent, bg=T.SURF, padx=px(16), pady=px(10))
        f.pack(side="left", padx=(0, px(10)))
        v = tk.Label(f, text="–", bg=T.SURF, fg=color, font=T.font("big"))
        v.pack(anchor="w")
        tk.Label(f, text=label, bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(anchor="w")
        return v

    def on_show(self) -> None:
        self.detect(quiet=True)

    def detect(self, quiet: bool = False) -> None:
        cards = importer.find_card_sources()
        values = [str(c) for c in cards]
        self.source_cb.configure(values=values)
        if cards and (not self.source_var.get() or self.source_var.get() not in values and quiet):
            self.source_var.set(values[0])
        if cards:
            self.source_hint.configure(text=f"Found {len(cards)} card{'s' if len(cards) > 1 else ''}.", fg=T.PICK)
        else:
            self.source_hint.configure(text="No memory card found - plug one in and press ⟳, or choose a folder.",
                                       fg=T.MUTE)

    def pick_source(self) -> None:
        d = filedialog.askdirectory(title="Import from…", parent=self)
        if d:
            self.source_var.set(d)
            self._invalidate()

    def pick_dest(self) -> None:
        d = filedialog.askdirectory(title="Import into…", parent=self)
        if d:
            self.dest_var.set(d)
            self._invalidate()

    def _invalidate(self) -> None:
        self.plan = None
        self.import_btn.configure(state="disabled")

    def scan(self) -> None:
        src, dest = Path(self.source_var.get().strip()), Path(self.dest_var.get().strip())
        if not src.is_dir():
            return self.app.toast("Choose a card or folder to import from.", "warn")
        if not str(dest):
            return self.app.toast("Choose where to import to.", "warn")
        try:
            if dest.resolve() == src.resolve() or src.resolve() in dest.resolve().parents:
                return self.app.toast("The destination can't be inside the folder you're importing from.", "error")
        except OSError:
            pass
        bad = organizer.validate_template(self.tmpl_var.get() + self.rename_var.get())
        if bad:
            return self.app.toast("Unknown token(s): " + ", ".join("{" + b + "}" for b in bad), "error")

        def work(progress, stop):
            return importer.plan_import(src, dest, self.tmpl_var.get().strip(), self.rename_var.get().strip(),
                                        self.videos_var.get(), self.skip_var.get(), progress_cb=progress,
                                        should_stop=stop)

        self.app.run_task("Reading card…", work, self._show_plan)

    def _show_plan(self, plan: importer.ImportPlan) -> None:
        self.plan = plan
        T.count_up(self.stat_new, len(plan.to_copy))
        T.count_up(self.stat_dup, plan.duplicates)
        self.stat_size.configure(text=importer.human_bytes(plan.bytes_to_copy))
        self.tree.delete(*self.tree.get_children())
        nodes = {}
        for folder, n in plan.folders().items():
            parts = [] if folder == "." else Path(folder).parts
            parent, acc = "", ""
            for part in parts[:-1]:
                acc = os.path.join(acc, part)
                if acc not in nodes:
                    nodes[acc] = self.tree.insert(parent, "end", text=part, open=True)
                parent = nodes[acc]
            self.tree.insert(parent, "end", text=parts[-1] if parts else str(plan.dest_root), values=(n,))
        self.import_btn.configure(state="normal" if plan.to_copy else "disabled")
        if not plan.units:
            self.app.toast("No photos found there.", "info")
        elif not plan.to_copy:
            self.app.toast("Everything on this card is already in your library.", "ok")

    def run_import(self) -> None:
        plan = self.plan
        if not plan or not plan.to_copy:
            return
        free = None
        try:
            import shutil

            plan.dest_root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(plan.dest_root).free
        except OSError:
            pass
        if free is not None and free < plan.bytes_to_copy * 1.02:
            return messagebox.showerror("Not enough space",
                                        f"Need {importer.human_bytes(plan.bytes_to_copy)}, only "
                                        f"{importer.human_bytes(free)} free on that drive.", parent=self)
        self.app.settings["import_dest"] = str(plan.dest_root)
        self.app.settings["import_template"] = self.tmpl_var.get().strip()

        def work(progress, stop):
            return importer.apply_import(plan, progress, stop)

        def done(res):
            n, journal, errors = res
            self._invalidate()
            msg = f"Imported {n} photos into {plan.dest_root.name}."
            if errors:
                msg += f" {len(errors)} problems - see History."
            self.app.toast(msg, "warn" if errors else "ok")
            if self.open_var.get() and n:
                folders = {u.dest_dir for u in plan.to_copy}
                target = folders.pop() if len(folders) == 1 else plan.dest_root
                if target == plan.dest_root:
                    self.app.recursive_var.set(True)
                    self.app.settings["recursive"] = True
                self.app.open_folder(target)

        self.app.run_task("Importing", work, done)
