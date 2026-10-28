"""Export screen: copy/move picks, XMP for Lightroom, rejects, share-ready JPEGs, reports."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from ..core import export as ex
from ..core import file_operations as fo
from ..core.library import FLAG_PICK, FLAG_REJECT
from . import theme as T
from .common import header
from .theme import px

class ExportView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        sf = T.ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        root = sf.inner
        header(root, "Export & deliver", "Copy your selects somewhere, hand ratings to Lightroom / Capture One, "
                                          "deal with rejects, or make a contact sheet to share.")
        grid = tk.Frame(root, bg=T.BG)
        grid.pack(fill="both", expand=True, padx=px(24), pady=(0, px(24)))
        grid.columnconfigure(0, weight=1, uniform="e")
        grid.columnconfigure(1, weight=1, uniform="e")

        # --- copy / move
        c = T.Card(grid, "Copy or move photos")
        c.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, px(12)), pady=(0, px(12)))
        b = c.body
        r = tk.Frame(b, bg=T.SURF)
        r.pack(fill="x")
        tk.Label(r, text="Photos", bg=T.SURF, fg=T.MUTE, width=12, anchor="w").pack(side="left")
        self.sel_cb = ttk.Combobox(r, state="readonly", values=list(ex.SELECTIONS.values()))
        self.sel_cb.set(ex.SELECTIONS["picks"])
        self.sel_cb.pack(side="left", fill="x", expand=True)
        self.sel_cb.bind("<<ComboboxSelected>>", lambda e: self.on_show())
        self.sel_count = tk.Label(b, text="", bg=T.SURF, fg=T.ACC, font=T.font("small"), anchor="w")
        self.sel_count.pack(fill="x", pady=(px(2), px(8)))
        r = tk.Frame(b, bg=T.SURF)
        r.pack(fill="x")
        tk.Label(r, text="Destination", bg=T.SURF, fg=T.MUTE, width=12, anchor="w").pack(side="left")
        self.dest_var = tk.StringVar()
        self._dest_auto = True
        de = ttk.Entry(r, textvariable=self.dest_var)
        de.pack(side="left", fill="x", expand=True)
        de.bind("<Key>", lambda e: setattr(self, "_dest_auto", False))
        ttk.Button(r, text="…", width=3, command=self.pick_dest).pack(side="left", padx=(px(4), 0))
        r = tk.Frame(b, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        self.mode_var = tk.StringVar(value="copy")
        ttk.Radiobutton(r, text="Copy (originals stay)", value="copy", variable=self.mode_var,
                        style="Card.TRadiobutton").pack(side="left")
        ttk.Radiobutton(r, text="Move", value="move", variable=self.mode_var, style="Card.TRadiobutton").pack(
            side="left", padx=px(14))
        self.inc_raw = tk.BooleanVar(value=True)
        self.inc_jpg = tk.BooleanVar(value=True)
        self.inc_side = tk.BooleanVar(value=True)
        self.inc_xmp = tk.BooleanVar(value=False)
        for var, text in ((self.inc_raw, "Include RAW files"), (self.inc_jpg, "Include JPEG / other files"),
                          (self.inc_side, "Include sidecars (.xmp etc.)"),
                          (self.inc_xmp, "Write ratings/labels to XMP first")):
            ttk.Checkbutton(b, text=text, variable=var, style="Card.TCheckbutton").pack(anchor="w")
        self.del_after = tk.BooleanVar(value=False)
        self.del_cb = ttk.Checkbutton(b, text="Then delete the originals from this folder (Recycle Bin)",
                                      variable=self.del_after, style="Card.TCheckbutton")
        self.del_cb.pack(anchor="w")
        T.tip(self.del_cb, "After copying, each original is checked against its copy (same size) and only then sent "
                           "to the Recycle Bin. Anything that didn't copy is kept. With 'Move' this isn't needed.")
        self.mode_var.trace_add("write", lambda *a: self.del_cb.configure(
            state="disabled" if self.mode_var.get() == "move" else "normal"))
        r = tk.Frame(b, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        tk.Label(r, text="Subfolders", bg=T.SURF, fg=T.MUTE, width=12, anchor="w").pack(side="left")
        self.sub_var = tk.StringVar(value="")
        e = ttk.Entry(r, textvariable=self.sub_var)
        e.pack(side="left", fill="x", expand=True)
        T.tip(e, "Optional, e.g. {date} or {rating}. Same tokens as Organise.")
        r = tk.Frame(b, bg=T.SURF)
        r.pack(fill="x", pady=(px(4), 0))
        tk.Label(r, text="Rename to", bg=T.SURF, fg=T.MUTE, width=12, anchor="w").pack(side="left")
        self.ren_var = tk.StringVar(value="")
        e = ttk.Entry(r, textvariable=self.ren_var)
        e.pack(side="left", fill="x", expand=True)
        T.tip(e, "Optional, e.g. Smith_Wedding_{seq:3}. Leave empty to keep names.")
        ttk.Button(b, text="Export", style="Accent.TButton", command=self.export).pack(anchor="w", pady=(px(12), 0))

        # --- xmp
        c = T.Card(grid, "Send ratings to Lightroom / Capture One / Bridge",
                   "Writes an .xmp sidecar next to each decided photo (IMG_0001.xmp - the name Lightroom looks "
                   "for). Existing edits in sidecars are kept. Rejects get Lightroom's 'rejected' rating.")
        c.grid(row=0, column=1, sticky="nsew", pady=(0, px(12)))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        tk.Label(r, text="Label picks as", bg=T.SURF, fg=T.MUTE).pack(side="left")
        self.pick_label = ttk.Combobox(r, state="readonly", values=["(no label)", "Green", "Red", "Yellow", "Blue", "Purple"],
                                       width=12)
        self.pick_label.set("Green")
        self.pick_label.pack(side="left", padx=px(8))
        ttk.Button(c.body, text="Write XMP sidecars", command=self.write_xmp).pack(anchor="w", pady=(px(10), 0))
        tk.Label(c.body, text="In Lightroom: select the photos › Metadata › Read Metadata from Files.",
                 bg=T.SURF, fg=T.FAINT, font=T.font("tiny"), anchor="w").pack(fill="x", pady=(px(6), 0))

        # --- rejects
        c = T.Card(grid, "Rejects", "Get rejected photos out of the way. Moving to a folder can be undone from "
                                    "History; the Recycle Bin can be restored from Windows.")
        c.grid(row=1, column=1, sticky="nsew", pady=(0, px(12)))
        self.rej_count = tk.Label(c.body, text="", bg=T.SURF, fg=T.REJECT, font=T.font("small"), anchor="w")
        self.rej_count.pack(fill="x", pady=(0, px(6)))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        ttk.Button(r, text="Move to _Rejected folder", command=lambda: self.rejects("folder")).pack(side="left")
        ttk.Button(r, text="Send to Recycle Bin", style="Danger.TButton",
                   command=lambda: self.rejects("trash")).pack(side="left", padx=px(8))

        # --- already exported
        c = T.Card(grid, "Delete originals you've already exported",
                   "Pick the folder you exported to. Photos here that have an identical copy there (same name and "
                   "size, RAW+JPEG both) go to the Recycle Bin. Photos without a matching copy are never touched.")
        c.grid(row=2, column=1, sticky="nsew", pady=(0, px(12)))
        ttk.Button(c.body, text="Choose export folder…", style="Danger.TButton",
                   command=self.delete_already_exported).pack(anchor="w")

        # --- share-ready JPEGs
        c = T.Card(grid, "Share-ready JPEGs",
                   "Resized, sharpened for screens and optionally watermarked - for Instagram, email or a client "
                   "gallery. Works from RAW too. Uses the photo selection above.")
        c.grid(row=2, column=0, sticky="nsew", padx=(0, px(12)), pady=(0, px(12)))
        from ..core import web_export as we

        self._we = we
        cols = tk.Frame(c.body, bg=T.SURF)
        cols.pack(fill="x")
        form = tk.Frame(cols, bg=T.SURF)
        form.pack(side="left", fill="x", expand=True)
        self.web_canvas = tk.Canvas(cols, width=px(200), height=px(140), bg=T.GRID_BG, highlightthickness=0)
        self.web_canvas.pack(side="right", padx=(px(12), 0))
        self.web_canvas.create_text(px(100), px(70), text="Preview", fill=T.FAINT, font=T.font("small"))

        def row(label):
            r = tk.Frame(form, bg=T.SURF)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, bg=T.SURF, fg=T.MUTE, width=11, anchor="w").pack(side="left")
            return r

        self.web_size = ttk.Combobox(row("Size"), state="readonly", values=list(we.SIZE_PRESETS.keys()), width=22)
        self.web_size.set("Web / email (2048 px)")
        self.web_size.pack(side="left", fill="x", expand=True)
        r = row("Quality")
        self.web_q = tk.IntVar(value=88)
        ttk.Spinbox(r, from_=50, to=100, width=5, textvariable=self.web_q).pack(side="left")
        self.web_exif = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text="Keep camera info", variable=self.web_exif, style="Card.TCheckbutton").pack(
            side="left", padx=px(10))
        r = row("Watermark")
        self.web_wm = tk.StringVar(value=self.app.settings.get("watermark", ""))
        e = ttk.Entry(r, textvariable=self.web_wm)
        e.pack(side="left", fill="x", expand=True)
        T.tip(e, "e.g. © Your Name. Leave empty for none.")
        r = row("Position")
        self.web_pos = ttk.Combobox(r, state="readonly", values=we.POSITIONS, width=12)
        self.web_pos.set("bottom-right")
        self.web_pos.pack(side="left")
        self.web_op = tk.DoubleVar(value=0.6)
        ttk.Scale(r, from_=0.2, to=1.0, variable=self.web_op).pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(10), 0))
        ttk.Button(r, text="Export JPEGs", style="Accent.TButton", command=self.export_web).pack(side="left")
        ttk.Button(r, text="Save selected as full-size JPG…", style="Small.TButton",
                   command=lambda: self.app.save_selected_as_jpg(self.app.views["cull"].grid.selected_photos())
                   ).pack(side="left", padx=px(8))
        ttk.Button(r, text="Preview", command=self.preview_web).pack(side="left", padx=px(8))
        self.web_dest_hint = tk.Label(r, text="", bg=T.SURF, fg=T.FAINT, font=T.font("tiny"))
        self.web_dest_hint.pack(side="left")

        # --- reports
        c = T.Card(grid, "Reports & lists")
        c.grid(row=3, column=0, columnspan=2, sticky="nsew")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        ttk.Button(r, text="HTML contact sheet…", command=self.html).pack(side="left")
        ttk.Button(r, text="CSV spreadsheet…", command=self.csv).pack(side="left", padx=px(8))
        ttk.Button(r, text="File list (.txt)…", command=self.txt).pack(side="left")
        ttk.Button(r, text="Copy names for Lightroom filter", command=self.copy_names).pack(side="left", padx=px(8))
        tk.Label(c.body, text="Reports use the photo selection chosen on the left.", bg=T.SURF, fg=T.FAINT,
                 font=T.font("tiny"), anchor="w").pack(fill="x", pady=(px(6), 0))

    def _which(self) -> str:
        return next((k for k, v in ex.SELECTIONS.items() if v == self.sel_cb.get()), "picks")

    def on_show(self) -> None:
        lib = self.app.lib
        if not lib:
            self.sel_count.configure(text="")
            self.rej_count.configure(text="")
            return
        n = len(ex.select(lib, self._which()))
        self.sel_count.configure(text=f"{n} photo{'s' if n != 1 else ''}")
        if not self.dest_var.get() or self._dest_auto:
            # Next to the shoot, not inside it (so exports don't show up as duplicates).
            self.dest_var.set(str(lib.root.parent / f"{lib.root.name} - Selected"))
            self._dest_auto = True
        nrej = sum(1 for p in lib.photos if p.flag == FLAG_REJECT)
        self.rej_count.configure(text=f"{nrej} rejected photos" if nrej else "No rejects yet")

    def pick_dest(self) -> None:
        d = filedialog.askdirectory(title="Export to…", parent=self)
        if d:
            self.dest_var.set(d)
            self._dest_auto = False

    def export(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        photos = ex.select(lib, self._which())
        if not photos:
            return self.app.toast("No photos match that selection yet.", "warn")
        dest = Path(self.dest_var.get().strip() or (lib.root.parent / f"{lib.root.name} - Selected"))
        if self.mode_var.get() == "move" and not messagebox.askyesno(
                "Move photos", f"Move {len(photos)} photos to\n{dest}?\n\nUndo is available in History.", parent=self):
            return
        delete_after = self.del_after.get() and self.mode_var.get() == "copy"
        if delete_after and not messagebox.askyesno(
                "Copy, then delete originals",
                f"Copy {len(photos)} photos to\n{dest}\n\nthen send the originals to the Recycle Bin?\n\n"
                "Each original is only deleted after its copy is verified.", icon="warning", parent=self):
            return
        opts = ex.ExportOptions(destination=dest, mode=self.mode_var.get(), include_raw=self.inc_raw.get(),
                                include_jpeg=self.inc_jpg.get(), include_sidecars=self.inc_side.get(),
                                subfolder_template=self.sub_var.get().strip(),
                                rename_template=self.ren_var.get().strip(), write_xmp=self.inc_xmp.get())

        def work(progress, stop):
            n, journal, errors = ex.export_photos(lib, photos, opts, progress, stop)
            deleted, kept = 0, []
            if delete_after and not stop():
                copies = ex.exported_copies_from_journal(journal)
                deleted, _j, kept = ex.delete_originals(lib, photos, copies=copies, progress_cb=progress)
            return n, journal, errors, deleted, kept

        def done(res):
            n, journal, errors, deleted, kept = res
            if opts.mode == "move":
                lib.migrate_keys(journal)
            msg = f"Exported {n} photos to {dest.name}."
            if delete_after:
                msg += f" Deleted {deleted} originals."
                if kept:
                    msg += f" {len(kept)} kept (no verified copy - e.g. RAW not included)."
            if errors:
                msg += f" {len(errors)} problems."
            if opts.mode == "move" or delete_after:
                self.app.reload_library(keep_view=True)
            self.app.toast(msg, "warn" if errors or kept else "ok")
            fo.open_in_file_manager(dest)

        self.app.run_task("Exporting…", work, done)

    def delete_already_exported(self) -> None:
        lib = self.app.lib
        if not lib:
            return self.app.toast("Open a folder first.", "warn")
        d = filedialog.askdirectory(title="Folder you exported to", parent=self,
                                    initialdir=self.dest_var.get() if Path(self.dest_var.get()).is_dir() else None)
        if not d:
            return
        dest = Path(d)
        if os.path.normcase(str(dest.resolve())) == os.path.normcase(str(lib.root.resolve())):
            return self.app.toast("That's this folder - choose the folder the copies are in.", "error")
        found = ex.find_exported(lib, dest)
        if not found:
            return self.app.toast(f"No photos here have a matching copy in {dest.name}.", "info")
        picks = sum(1 for p in found if p.flag == FLAG_PICK)
        if not messagebox.askyesno(
                "Delete exported originals",
                f"{len(found)} photos here have identical copies in\n{dest}\n({picks} of them are picks).\n\n"
                "Send these originals to the Recycle Bin?", icon="warning", parent=self):
            return

        def work(progress, stop):
            return ex.delete_originals(lib, found, destination=dest, progress_cb=progress)

        def done(res):
            n, journal, problems = res
            self.app.reload_library(keep_view=True)
            self.app.toast(f"Sent {n} exported originals to the Recycle Bin." +
                           (f" {len(problems)} kept or failed." if problems else ""), "warn" if problems else "ok")

        self.app.run_task("Deleting exported originals…", work, done)

    def _web_opts(self, dest: Path):
        we = self._we
        try:
            q = int(self.web_q.get())
        except (tk.TclError, ValueError):
            q = 88
        self.app.settings["watermark"] = self.web_wm.get()
        return we.WebExportOptions(destination=dest, long_edge=we.SIZE_PRESETS.get(self.web_size.get(), 2048),
                                   quality=max(50, min(100, q)), keep_exif=self.web_exif.get(),
                                   watermark=self.web_wm.get(), position=self.web_pos.get(),
                                   opacity=float(self.web_op.get()), rename_template=self.ren_var.get().strip())

    def preview_web(self) -> None:
        lib = self.app.lib
        photos = ex.select(lib, self._which()) if lib else []
        if not photos:
            return self.app.toast("No photos in that selection to preview.", "warn")
        p = self.app.views["cull"].grid.current if self.app.views["cull"].grid.current in photos else photos[0]
        opts = self._web_opts(Path("."))
        long_edge = opts.long_edge
        opts.long_edge = 1200  # preview at a sensible size, watermark scaled the same way
        img, _exif, err = self._we.render_one(p, opts)
        if img is None:
            return self.app.toast(f"Couldn't render preview: {err}", "error")
        from PIL import ImageTk

        cw, ch = self.web_canvas.winfo_width(), self.web_canvas.winfo_height()
        img.thumbnail((cw, ch))
        self._web_tk = ImageTk.PhotoImage(img)
        self.web_canvas.delete("all")
        self.web_canvas.create_image(cw // 2, ch // 2, image=self._web_tk)
        self.web_dest_hint.configure(text=f"{p.name} · {long_edge or 'full'} px")

    def export_web(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        photos = ex.select(lib, self._which())
        if not photos:
            return self.app.toast("No photos match that selection yet.", "warn")
        base = Path(self.dest_var.get().strip() or (lib.root.parent / f"{lib.root.name} - Selected"))
        dest = base / "Share-ready"
        opts = self._web_opts(dest)

        def work(progress, stop):
            return self._we.export_web(lib, photos, opts, progress, stop)

        def done(res):
            n, journal, errors = res
            self.app.toast(f"Made {n} share-ready JPEGs in {dest.name}." + (f" {len(errors)} failed." if errors else ""),
                           "warn" if errors else "ok")
            fo.open_in_file_manager(dest)

        self.app.run_task("Making JPEGs", work, done)

    def write_xmp(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        label = self.pick_label.get()
        pick_label = None if label.startswith("(") else label
        photos = list(lib.photos)

        def work(progress, stop):
            return ex.write_xmp_for(photos, pick_label=pick_label, progress_cb=progress, should_stop=stop)

        def done(res):
            n, errors = res
            self.app.toast(f"Wrote {n} XMP sidecars." + (f" {len(errors)} failed." if errors else ""),
                           "warn" if errors else "ok")

        self.app.run_task("Writing XMP", work, done)

    def rejects(self, action: str) -> None:
        lib = self.app.lib
        if not lib:
            return
        n = sum(1 for p in lib.photos if p.flag == FLAG_REJECT)
        if not n:
            return self.app.toast("No rejected photos.", "info")
        where = "the _Rejected folder" if action == "folder" else "the Recycle Bin"
        if not messagebox.askyesno("Rejects", f"Send {n} rejected photos (and their RAW/JPEG partners and sidecars) "
                                   f"to {where}?", parent=self):
            return

        def work(progress, stop):
            return ex.handle_rejects(lib, action, progress)

        def done(res):
            count, journal, errors = res
            lib.migrate_keys(journal)
            self.app.reload_library(keep_view=True)
            self.app.toast(f"{count} rejects moved to {where}." + (f" {len(errors)} problems." if errors else ""),
                           "warn" if errors else "ok")

        self.app.run_task("Handling rejects…", work, done)

    def _save_dialog(self, ext: str, name: str) -> Optional[Path]:
        lib = self.app.lib
        if not lib:
            return None
        f = filedialog.asksaveasfilename(parent=self, defaultextension=ext, initialdir=str(lib.root),
                                         initialfile=name, filetypes=[(ext.upper().strip("."), "*" + ext)])
        return Path(f) if f else None

    def html(self) -> None:
        lib = self.app.lib
        photos = ex.select(lib, self._which()) if lib else []
        if not photos:
            return self.app.toast("No photos in that selection.", "warn")
        path = self._save_dialog(".html", f"{lib.root.name} - contact sheet.html")
        if not path:
            return
        from ..core.html_report import generate_html_report

        def work(progress, stop):
            return generate_html_report(lib, photos, path, title=f"{lib.root.name} — {self.sel_cb.get()}")

        self.app.run_task("Building contact sheet…", work,
                          lambda p: (self.app.toast("Contact sheet saved.", "ok"), fo.open_with_default_app(p)))

    def csv(self) -> None:
        lib = self.app.lib
        photos = ex.select(lib, self._which()) if lib else []
        path = self._save_dialog(".csv", f"{lib.root.name} - culling.csv") if photos else None
        if path:
            ex.write_csv(photos, path)
            self.app.toast("CSV saved.", "ok")

    def txt(self) -> None:
        lib = self.app.lib
        photos = ex.select(lib, self._which()) if lib else []
        path = self._save_dialog(".txt", f"{lib.root.name} - files.txt") if photos else None
        if path:
            ex.write_file_list(photos, path)
            self.app.toast("File list saved.", "ok")

    def copy_names(self) -> None:
        lib = self.app.lib
        photos = ex.select(lib, self._which()) if lib else []
        if not photos:
            return
        self.clipboard_clear()
        self.clipboard_append(ex.lightroom_filter_text(photos))
        self.app.toast(f"Copied {len(photos)} names. In Lightroom use Library Filter › Text › Filename › Contains.", "ok")
