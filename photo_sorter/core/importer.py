"""
Import photos from a memory card (or any folder) into your photo library.

* Finds camera cards automatically (any drive/volume with a DCIM folder).
* Sorts into folders from the same templates as the Organiser
  (default ``{YYYY}/{YYYY}-{MM}-{DD}``), reading the capture date from EXIF.
* RAW+JPEG pairs and sidecars stay together.
* Skips anything already imported (same name and size already in the
  library), so you can re-insert the same card safely.
* Every copy is size-verified and journaled; nothing on the card is changed.
"""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import grouping, metadata
from .constants import ALL_EXTS
from .file_operations import Journal, Transaction
from .library import Photo, discover
from .organizer import TemplateContext, render, render_folder, sanitize_component

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mts", ".m2ts", ".mkv", ".3gp", ".m4v"}


def find_card_sources() -> List[Path]:
    """Folders that look like camera cards (contain DCIM)."""
    roots: List[Path] = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase[2:]:  # skip A: and B:
            root = Path(f"{letter}:\\")
            try:
                if (root / "DCIM").is_dir():
                    roots.append(root / "DCIM")
            except OSError:
                continue
    else:
        bases = [Path("/Volumes"), Path("/media"), Path("/run/media")]
        for base in bases:
            if not base.is_dir():
                continue
            for depth1 in base.iterdir():
                for cand in (depth1, *(depth1.iterdir() if depth1.is_dir() else [])):
                    try:
                        if cand.is_dir() and (cand / "DCIM").is_dir():
                            roots.append(cand / "DCIM")
                    except OSError:
                        continue
    return roots


@dataclass
class ImportUnit:
    photo: Photo  # a pseudo-photo (files + metadata) used for template rendering
    files: List[Path]
    dest_dir: Path
    new_stem: Optional[str] = None
    duplicate: bool = False
    is_video: bool = False

    @property
    def size(self) -> int:
        total = 0
        for f in self.files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total


@dataclass
class ImportPlan:
    source: Path
    dest_root: Path
    units: List[ImportUnit] = field(default_factory=list)

    @property
    def to_copy(self) -> List[ImportUnit]:
        return [u for u in self.units if not u.duplicate]

    @property
    def duplicates(self) -> int:
        return sum(1 for u in self.units if u.duplicate)

    @property
    def bytes_to_copy(self) -> int:
        return sum(u.size for u in self.to_copy)

    def folders(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for u in self.to_copy:
            try:
                rel = str(u.dest_dir.relative_to(self.dest_root)) or "."
            except ValueError:
                rel = str(u.dest_dir)
            out[rel] = out.get(rel, 0) + 1
        return dict(sorted(out.items()))


def _index_destination(dest_root: Path) -> set:
    idx = set()
    if not dest_root.is_dir():
        return idx
    for dirpath, dirnames, files in os.walk(dest_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in files:
            try:
                idx.add((fn.lower(), (Path(dirpath) / fn).stat().st_size))
            except OSError:
                pass
    return idx


def _videos(source: Path) -> List[Path]:
    out = []
    for dirpath, dirnames, files in os.walk(source):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in files:
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTS and not fn.startswith("._"):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def plan_import(
    source: Path,
    dest_root: Path,
    folder_template: str = "{YYYY}/{YYYY}-{MM}-{DD}",
    rename_template: str = "",
    include_videos: bool = True,
    skip_duplicates: bool = True,
    event_gap_minutes: float = 45.0,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> ImportPlan:
    source, dest_root = Path(source), Path(dest_root)
    plan = ImportPlan(source=source, dest_root=dest_root)
    photos = discover(source, recursive=True)
    vids = _videos(source) if include_videos else []
    total = len(photos) + len(vids)
    for i, p in enumerate(photos, start=1):
        if should_stop():
            return plan
        md = metadata.extract_metadata(p.display)
        if md.datetime_original is None and p.display != p.master:
            md = metadata.extract_metadata(p.master)
        p.meta = md
        dt = md.dt()
        p.time_from_exif = dt is not None
        if dt is None:
            try:
                dt = datetime.fromtimestamp(p.master.stat().st_mtime)
            except OSError:
                dt = None
        p.capture_time = dt
        if progress_cb and (i % 10 == 0 or i == total):
            progress_cb(i, total, p.name)
    for v in vids:
        try:
            when = datetime.fromtimestamp(v.stat().st_mtime)
        except OSError:
            when = None
        vp = Photo(key=f"video|{v}", master=v, files=[v], display=v, capture_time=when)
        photos.append(vp)

    existing = _index_destination(dest_root) if skip_duplicates else set()
    ctx = TemplateContext(events=grouping.split_moments([(p.key, p.capture_time) for p in photos], event_gap_minutes))
    ordered = sorted(photos, key=lambda p: (p.capture_time or datetime.max, p.name.lower()))
    per_folder: Dict[str, int] = {}
    for p in ordered:
        folder = render_folder(folder_template, p, ctx) if folder_template.strip() else Path()
        k = str(folder).lower()
        per_folder[k] = per_folder.get(k, 0) + 1
        ctx.seq[p.key] = per_folder[k]
        stem = sanitize_component(render(rename_template, p, ctx)) if rename_template.strip() else None
        dup = False
        if skip_duplicates:
            dup = True
            for f in p.files:
                try:
                    if (f.name.lower(), f.stat().st_size) not in existing:
                        dup = False
                        break
                except OSError:
                    dup = False
                    break
        plan.units.append(ImportUnit(photo=p, files=list(p.files), dest_dir=dest_root / folder, new_stem=stem,
                                     duplicate=dup, is_video=p.key.startswith("video|")))
    return plan


def apply_import(
    plan: ImportPlan,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> Tuple[int, Journal, List[str]]:
    """Copy the plan's files. Returns (photos imported, journal, errors)."""
    plan.dest_root.mkdir(parents=True, exist_ok=True)
    units = plan.to_copy
    done = 0
    with Transaction(plan.dest_root, kind="import", label=f"Import {len(units)} photos from {plan.source}") as tx:
        for i, u in enumerate(units, start=1):
            if should_stop():
                break
            ok = True
            for f in u.files:
                dst = u.dest_dir / ((u.new_stem + f.suffix) if u.new_stem else f.name)
                out = tx.copy(f, dst)
                try:
                    if out is None or out.stat().st_size != f.stat().st_size:
                        ok = False
                        tx.errors.append(f"{f.name}: copy could not be verified")
                except OSError as e:
                    ok = False
                    tx.errors.append(f"{f.name}: {e}")
            done += 1 if ok else 0
            if progress_cb:
                progress_cb(i, len(units), u.files[0].name)
    return done, tx.journal, tx.errors


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
