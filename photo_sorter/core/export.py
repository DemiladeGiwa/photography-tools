"""
Exporting results: copy/move picks, write XMP ratings, deal with rejects,
and produce CSV / text / HTML reports.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .file_operations import Journal, Transaction, write_xmp
from .library import FLAG_PICK, FLAG_REJECT, Library, Photo, PhotoFilter
from .organizer import TemplateContext, render, render_folder, sanitize_component
from . import grouping

SELECTIONS = {
    "picks": "Picks",
    "rated3": "3+ stars",
    "rated1": "Any star rating",
    "labelled": "Has a colour label",
    "not_rejected": "Everything except rejects",
    "rejects": "Rejects",
    "all": "All photos",
}


def select(lib: Library, which: str) -> List[Photo]:
    out = []
    for p in lib.photos:
        if which == "picks" and p.flag != FLAG_PICK:
            continue
        if which == "rated3" and p.rating < 3:
            continue
        if which == "rated1" and p.rating < 1:
            continue
        if which == "labelled" and not p.label:
            continue
        if which == "not_rejected" and p.flag == FLAG_REJECT:
            continue
        if which == "rejects" and p.flag != FLAG_REJECT:
            continue
        out.append(p)
    out.sort(key=lambda p: (p.capture_time or datetime.max, p.name.lower()))
    return out


@dataclass
class ExportOptions:
    destination: Path
    mode: str = "copy"  # copy | move
    include_raw: bool = True
    include_jpeg: bool = True
    include_sidecars: bool = True
    subfolder_template: str = ""
    rename_template: str = ""
    write_xmp: bool = False


def export_photos(
    lib: Library,
    photos: Sequence[Photo],
    opts: ExportOptions,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> Tuple[int, Journal, List[str]]:
    from .constants import RAW_EXTS

    dest = Path(opts.destination)
    ctx = TemplateContext(events=grouping.split_moments([(p.key, p.capture_time) for p in lib.photos]))
    per_folder: dict = {}
    for p in photos:
        f = str(render_folder(opts.subfolder_template, p, ctx)).lower() if opts.subfolder_template else ""
        per_folder[f] = per_folder.get(f, 0) + 1
        ctx.seq[p.key] = per_folder[f]
    done = 0
    total = len(photos)
    with Transaction(lib.root, kind="export", label=f"{opts.mode.title()} {total} photos to {dest.name}") as tx:
        for i, p in enumerate(photos, start=1):
            if should_stop():
                break
            sub = render_folder(opts.subfolder_template, p, ctx) if opts.subfolder_template else Path()
            stem = sanitize_component(render(opts.rename_template, p, ctx)) if opts.rename_template else None
            files = [f for f in p.files
                     if (f.suffix.lower() in RAW_EXTS and opts.include_raw)
                     or (f.suffix.lower() not in RAW_EXTS and opts.include_jpeg)]
            if not files:
                files = [p.master]
            ok = True
            for f in files:
                if opts.write_xmp:
                    _write_decision_xmp(p, f)
                dst = dest / sub / ((stem + f.suffix) if stem else f.name)
                if opts.mode == "move":
                    r = tx.move(f, dst, with_sidecars=opts.include_sidecars)
                else:
                    r = tx.copy(f, dst, with_sidecars=opts.include_sidecars)
                ok = ok and r is not None
            done += 1 if ok else 0
            if progress_cb:
                progress_cb(i, total, p.name)
    return done, tx.journal, tx.errors


def _write_decision_xmp(p: Photo, f: Path) -> None:
    from .constants import RAW_EXTS

    rating = -1 if p.flag == FLAG_REJECT else p.rating
    label = p.label or ("Green" if p.flag == FLAG_PICK and not p.rating else None)
    if f.suffix.lower() in RAW_EXTS or len(p.files) == 1:
        write_xmp(f, rating, label)


def write_xmp_for(photos: Sequence[Photo], pick_label: Optional[str] = None,
                  only_decided: bool = True,
                  progress_cb: Optional[Callable[[int, int, str], None]] = None,
                  should_stop: Callable[[], bool] = lambda: False) -> Tuple[int, List[str]]:
    """Write rating/label sidecars. Rejects become rating -1 (Lightroom's 'rejected')."""
    count, errors = 0, []
    todo = [p for p in photos if not only_decided or p.rating or p.label or p.flag]
    for i, p in enumerate(todo, start=1):
        if should_stop():
            break
        if progress_cb and (i % 25 == 0 or i == len(todo)):
            progress_cb(i, len(todo), p.name)
        rating = -1 if p.flag == FLAG_REJECT else p.rating
        label = p.label or (pick_label if p.flag == FLAG_PICK else None)
        try:
            write_xmp(p.master, rating, label)
            count += 1
        except OSError as e:
            errors.append(f"{p.name}: {e}")
    return count, errors


def handle_rejects(lib: Library, action: str = "folder",
                   progress_cb: Optional[Callable[[int, int, str], None]] = None) -> Tuple[int, Journal, List[str]]:
    """action: 'folder' (move to _Rejected, undoable) or 'trash' (Recycle Bin)."""
    from .constants import REJECTED_DIRNAME

    rejects = [p for p in lib.photos if p.flag == FLAG_REJECT]
    n = 0
    label = "Move rejects to _Rejected" if action == "folder" else "Send rejects to Recycle Bin"
    with Transaction(lib.root, kind="rejects", label=label) as tx:
        for i, p in enumerate(rejects, start=1):
            ok = True
            for f in p.files:
                if action == "trash":
                    ok = tx.trash(f) and ok
                else:
                    ok = tx.move(f, lib.root / REJECTED_DIRNAME / f.name) is not None and ok
            n += 1 if ok else 0
            if progress_cb:
                progress_cb(i, len(rejects), p.name)
    from .file_operations import remove_emptied_dirs

    remove_emptied_dirs(tx.journal, lib.root)
    return n, tx.journal, tx.errors


# ------------------------------------------------------------------ delete exported originals

def _same_file(src: Path, dst: Path) -> bool:
    """A copy counts as safe only if it exists and has exactly the same size."""
    try:
        return dst.is_file() and dst.stat().st_size == src.stat().st_size and dst.stat().st_size > 0
    except OSError:
        return False


def exported_copies_from_journal(journal: Journal) -> dict:
    """Map original path -> copy path for every completed copy in an export journal."""
    out = {}
    for e in journal.entries:
        if e.op == "copy" and e.status == "done":
            out[os.path.normcase(os.path.abspath(e.src))] = Path(e.dst)
    return out


def find_exported(lib: Library, destination: Path, photos: Optional[Sequence[Photo]] = None) -> List[Photo]:
    """Photos whose every file already has an identical-size copy (same name) somewhere in ``destination``."""
    destination = Path(destination)
    index = {}
    if destination.is_dir():
        for dirpath, _dirs, files in os.walk(destination):
            for fn in files:
                p = Path(dirpath) / fn
                try:
                    index.setdefault((fn.lower(), p.stat().st_size), p)
                except OSError:
                    pass
    root = os.path.normcase(os.path.abspath(lib.root))
    dest_abs = os.path.normcase(os.path.abspath(destination))
    found = []
    for p in (photos if photos is not None else lib.photos):
        if os.path.normcase(os.path.abspath(p.master)).startswith(dest_abs + os.sep):
            continue  # the photo *is* the copy (export folder inside the library)
        ok = True
        for f in p.files:
            try:
                size = f.stat().st_size
            except OSError:
                ok = False
                break
            if size == 0 or (f.name.lower(), size) not in index:
                ok = False
                break
        if ok and root != dest_abs:
            found.append(p)
    return found


def delete_originals(
    lib: Library,
    photos: Sequence[Photo],
    copies: Optional[dict] = None,
    destination: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[int, Journal, List[str]]:
    """Send originals of already-exported photos to the Recycle Bin.

    Every file is re-verified first: it must have a same-size copy, either from
    ``copies`` (an export journal's src->dst map) or by name in ``destination``.
    Photos that fail the check are skipped, never deleted.
    """
    from .constants import EXPORTED_TRASH_DIRNAME

    verified_by_name = {p.key for p in find_exported(lib, destination, photos)} if destination else set()
    n, skipped = 0, []
    with Transaction(lib.root, kind="delete-exported", label=f"Delete {len(photos)} exported originals") as tx:
        for i, p in enumerate(photos, start=1):
            safe = p.key in verified_by_name
            if not safe and copies:
                safe = all(
                    (c := copies.get(os.path.normcase(os.path.abspath(f)))) is not None and _same_file(f, c)
                    for f in p.files
                )
            if not safe:
                skipped.append(f"{p.name}: no verified copy found - kept")
            else:
                ok = True
                for f in p.files:
                    ok = tx.trash(f, fallback_dirname=EXPORTED_TRASH_DIRNAME) and ok
                n += 1 if ok else 0
            if progress_cb:
                progress_cb(i, len(photos), p.name)
    from .file_operations import remove_emptied_dirs

    remove_emptied_dirs(tx.journal, lib.root)
    return n, tx.journal, tx.errors + skipped


def write_csv(photos: Sequence[Photo], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["File", "Folder", "Type", "Captured", "Flag", "Stars", "Label", "Quality", "Sharpness",
                    "Exposure", "Sharpness verdict", "Exposure verdict", "Group", "Best in group",
                    "Camera", "Lens", "ISO", "Aperture", "Shutter", "Focal length", "Notes", "Path"])
        for p in photos:
            q, md = p.quality, p.meta
            w.writerow([
                p.name, p.rel_dir or ".", p.type_label,
                p.capture_time.strftime("%Y-%m-%d %H:%M:%S") if p.capture_time else "",
                {FLAG_PICK: "Pick", FLAG_REJECT: "Reject"}.get(p.flag or "", ""),
                p.rating or "", p.label or "",
                f"{p.quality_score:.1f}" if q else "", f"{p.sharpness_score:.1f}" if q else "",
                f"{p.exposure_score:.1f}" if q else "", q.sharpness_label if q else "", q.exposure_label if q else "",
                p.group_id or "", "yes" if p.is_best else "",
                md.model or "", md.lens or "", md.iso or "", f"f/{md.f_number:.1f}" if md.f_number else "",
                md.exposure_time or "", f"{md.focal_length:.0f}mm" if md.focal_length else "",
                "; ".join((q.reasons if q else [])[:3]) or (p.error or ""),
                str(p.master),
            ])


def write_file_list(photos: Sequence[Photo], path: Path, names_only: bool = False) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for p in photos:
            f.write((p.name if names_only else str(p.master)) + "\n")


def lightroom_filter_text(photos: Sequence[Photo]) -> str:
    """Stems joined for pasting into Lightroom's Library Filter (Text > Filename > Contains)."""
    return " ".join(sorted({p.stem for p in photos}))
