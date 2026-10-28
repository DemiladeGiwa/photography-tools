"""
Organiser: sort photos into folders and/or rename them from templates.

Templates use ``{tokens}``; ``/`` in a folder template creates subfolders.
Examples::

    {YYYY}/{YYYY}-{MM}-{DD}            -> 2026/2026-09-20/
    {camera}/{YYYY}-{MM}               -> Canon EOS R6/2026-09/
    {date} Event {event}               -> 2026-09-20 Event 03/
    {flag}                             -> Picks/ , Rejects/ , Unflagged/
    rename: {YYYY}{MM}{DD}_{seq:4}     -> 20260920_0001.CR2 (+ .JPG, + .xmp)

Everything is planned first (``plan_organize``) so the UI can show a full
before/after preview; ``apply_plan`` then runs it through a journaled,
undoable, collision-safe transaction. RAW+JPEG pairs and sidecars always
move together and keep matching names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import grouping
from .file_operations import Journal, Transaction, remove_emptied_dirs
from .library import FLAG_PICK, FLAG_REJECT, Library, Photo

TOKENS_HELP = [
    ("{YYYY} {YY} {MM} {DD}", "capture year / month / day"),
    ("{MonthName} {Mon}", "September / Sep"),
    ("{hh} {mm} {ss}", "capture time"),
    ("{date}", "YYYY-MM-DD"),
    ("{camera} {make} {lens}", "from EXIF"),
    ("{event}", "event number (split by time gaps)"),
    ("{group}", "burst/similar group, or 'Singles'"),
    ("{flag} {rating} {label}", "your culling decisions"),
    ("{type} {orientation} {iso}", "RAW+JPG, Portrait, 400"),
    ("{name} {folder} {seq} {seq:4}", "original name, current folder, counter"),
]

FOLDER_PRESETS = {
    "Year / Date": "{YYYY}/{YYYY}-{MM}-{DD}",
    "Year / Month": "{YYYY}/{YYYY}-{MM} {MonthName}",
    "Date": "{YYYY}-{MM}-{DD}",
    "Events (time gaps)": "{date} Event {event}",
    "Camera / Date": "{camera}/{YYYY}-{MM}-{DD}",
    "Picks / Rejects": "{flag}",
    "Star rating": "{rating}",
    "Colour label": "{label}",
    "Bursts & groups": "{group}",
    "File type (RAW / JPG)": "{type}",
    "Orientation": "{orientation}",
    "Keep current folders": "{folder}",
}

RENAME_PRESETS = {
    "Keep original names": "",
    "Date_Time": "{YYYY}{MM}{DD}_{hh}{mm}{ss}",
    "Date_Counter": "{YYYY}-{MM}-{DD}_{seq:4}",
    "Date_Original": "{YYYY}{MM}{DD}_{name}",
    "Event_Counter": "Event{event}_{seq:4}",
}

_ILLEGAL = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_TOKEN = re.compile(r"\{([A-Za-z]+)(?::(\d+))?\}")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(s: str) -> str:
    s = _ILLEGAL.sub("_", s).strip().rstrip(". ")
    if s.upper() in _RESERVED:
        s = f"_{s}"
    return s[:120] or "_"


@dataclass
class TemplateContext:
    events: Dict[str, int] = field(default_factory=dict)
    seq: Dict[str, int] = field(default_factory=dict)


def _values(p: Photo, ctx: TemplateContext) -> Dict[str, str]:
    t: Optional[datetime] = p.capture_time
    md = p.meta
    vals: Dict[str, str] = {}
    if t:
        vals.update(YYYY=f"{t.year:04d}", YY=f"{t.year % 100:02d}", MM=f"{t.month:02d}", DD=f"{t.day:02d}",
                    hh=f"{t.hour:02d}", mm=f"{t.minute:02d}", ss=f"{t.second:02d}",
                    MonthName=t.strftime("%B"), Mon=t.strftime("%b"), date=t.strftime("%Y-%m-%d"))
    else:
        for k in ("YYYY", "YY", "MM", "DD", "hh", "mm", "ss", "MonthName", "Mon"):
            vals[k] = "Undated" if k in ("YYYY", "MonthName") else "00"
        vals["date"] = "Undated"
    model = (md.model or "").strip()
    make = (md.make or "").strip()
    if make and model.lower().startswith(make.split()[0].lower()):
        camera = model
    else:
        camera = f"{make} {model}".strip()
    vals["camera"] = camera or "Unknown Camera"
    vals["make"] = make or "Unknown Make"
    vals["lens"] = (md.lens or "Unknown Lens").strip()
    vals["iso"] = str(md.iso) if md.iso else "ISO-unknown"
    ev = ctx.events.get(p.key, 0)
    vals["event"] = f"{ev:02d}" if ev else "Undated"
    vals["group"] = f"Group {p.group_id:03d}" if p.group_id else "Singles"
    vals["flag"] = {FLAG_PICK: "Picks", FLAG_REJECT: "Rejects"}.get(p.flag or "", "Unflagged")
    vals["rating"] = f"{p.rating} star{'s' if p.rating != 1 else ''}" if p.rating else "Unrated"
    vals["label"] = p.label or "No Label"
    vals["type"] = p.type_label
    a = p.aspect
    vals["orientation"] = "Square" if 0.95 <= a <= 1.05 else ("Landscape" if a > 1 else "Portrait")
    vals["name"] = p.stem
    vals["folder"] = p.rel_dir.replace("\\", "/")
    vals["ext"] = p.master.suffix.lstrip(".").upper()
    vals["seq"] = str(ctx.seq.get(p.key, 0))
    return vals


def render(template: str, p: Photo, ctx: TemplateContext) -> str:
    vals = _values(p, ctx)

    def sub(m: re.Match) -> str:
        name, width = m.group(1), m.group(2)
        if name == "seq":
            n = ctx.seq.get(p.key, 0)
            return f"{n:0{int(width)}d}" if width else str(n)
        if name in vals:
            return vals[name]
        return m.group(0)

    return _TOKEN.sub(sub, template)


def render_folder(template: str, p: Photo, ctx: TemplateContext) -> Path:
    text = render(template, p, ctx).replace("\\", "/")
    parts = [sanitize_component(x) for x in text.split("/") if x.strip() and x.strip() not in (".", "..")]
    return Path(*parts) if parts else Path()


def validate_template(template: str) -> List[str]:
    known = set(_values(Photo(key="x", master=Path("x.jpg"), files=[Path("x.jpg")], display=Path("x.jpg")),
                        TemplateContext()).keys()) | {"seq"}
    return [m.group(1) for m in _TOKEN.finditer(template) if m.group(1) not in known]


@dataclass
class PlanItem:
    photo_key: str
    moves: List[Tuple[Path, Path]]  # (src, dst) for each image file of the photo

    @property
    def changed(self) -> bool:
        return any(os.path.normcase(str(a)) != os.path.normcase(str(b)) for a, b in self.moves)


@dataclass
class OrganizePlan:
    items: List[PlanItem]
    mode: str  # "move" | "copy"
    target_root: Path
    unchanged: int = 0
    collisions: int = 0

    @property
    def file_count(self) -> int:
        return sum(len(i.moves) for i in self.items)

    def folders(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for it in self.items:
            dst = it.moves[0][1]
            try:
                rel = str(dst.parent.relative_to(self.target_root)) or "."
            except ValueError:
                rel = str(dst.parent)
            out[rel] = out.get(rel, 0) + 1
        return dict(sorted(out.items()))


def plan_organize(
    lib: Library,
    photos: Sequence[Photo],
    folder_template: str,
    rename_template: str = "",
    target_root: Optional[Path] = None,
    mode: str = "move",
    event_gap_minutes: float = 45.0,
    seq_start: int = 1,
) -> OrganizePlan:
    target_root = Path(target_root) if target_root else lib.root
    ordered = sorted(photos, key=lambda p: (p.capture_time or datetime.max, p.name.lower()))
    ctx = TemplateContext()
    ctx.events = grouping.split_moments([(p.key, p.capture_time) for p in lib.photos], event_gap_minutes)
    # {seq} restarts in every destination folder, so Date_Counter gives 0001.. per day.
    per_folder: Dict[str, int] = {}
    folder_of: Dict[str, Path] = {}
    for p in ordered:
        folder = render_folder(folder_template, p, ctx) if folder_template.strip() else Path()
        folder_of[p.key] = folder
        k = str(folder).lower()
        per_folder[k] = per_folder.get(k, seq_start - 1) + 1
        ctx.seq[p.key] = per_folder[k]

    items: List[PlanItem] = []
    planned: Dict[str, int] = {}
    collisions = unchanged = 0
    for p in ordered:
        dest_dir = target_root / folder_of[p.key]
        stem = sanitize_component(render(rename_template, p, ctx)) if rename_template.strip() else None
        moves = []
        for f in p.files:
            new_name = (stem + f.suffix) if stem else f.name
            dst = dest_dir / new_name
            k = os.path.normcase(str(dst))
            same = os.path.normcase(str(f)) == k
            if not same and (k in planned or dst.exists()):
                collisions += 1
            planned[k] = planned.get(k, 0) + 1
            moves.append((f, dst))
        item = PlanItem(p.key, moves)
        if not item.changed and mode == "move":
            unchanged += 1
            continue
        items.append(item)
    return OrganizePlan(items=items, mode=mode, target_root=target_root, unchanged=unchanged, collisions=collisions)


def apply_plan(
    lib: Library,
    plan: OrganizePlan,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
    label: str = "",
    remove_empty_folders: bool = False,
) -> Tuple[Journal, List[str]]:
    total = len(plan.items)
    with Transaction(lib.root, kind="organize", label=label or f"Organise {total} photos") as tx:
        for i, item in enumerate(plan.items, start=1):
            if should_stop():
                break
            for src, dst in item.moves:
                if plan.mode == "copy":
                    tx.copy(src, dst)
                else:
                    tx.move(src, dst)
            if progress_cb:
                progress_cb(i, total, item.moves[0][0].name)
    if remove_empty_folders and plan.mode == "move":
        remove_emptied_dirs(tx.journal, lib.root)
    return tx.journal, tx.errors


def remove_empty_subfolders(root: Path) -> int:
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        if d == Path(root) or any(part.startswith(".") for part in d.relative_to(root).parts):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def plan_flatten(lib: Library, photos: Optional[Sequence[Photo]] = None) -> OrganizePlan:
    """Move every photo from subfolders back into the library root (replaces the old PowerShell script)."""
    scope = [p for p in (photos if photos is not None else lib.photos) if p.rel_dir]
    return plan_organize(lib, scope, folder_template="", rename_template="", target_root=lib.root, mode="move")
