"""
Safe file operations with an on-disk transaction journal, plus XMP sidecars.

Rules this module enforces:
* Nothing is ever overwritten - collisions get " (1)", " (2)", ... suffixes.
* Every move/copy/rename is journaled *before* it happens and the journal is
  flushed after each step, so a crash leaves an accurate record.
* Undo replays the journal backwards and re-checks the filesystem first.
* "Delete" means Recycle Bin / Trash. If that is unavailable the file is
  moved into a ``_Rejected`` folder instead - never permanently deleted.
* XMP sidecars are *merged*: existing Lightroom/Capture One edits are kept,
  only rating / label are updated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .constants import CACHE_DIRNAME, JOURNAL_DIRNAME, RAW_EXTS, REJECTED_DIRNAME, SIDECAR_EXTS

try:  # optional, cross-platform trash support
    from send2trash import send2trash as _send2trash  # type: ignore
except Exception:  # pragma: no cover
    _send2trash = None


# ------------------------------------------------------------------ journal

@dataclass
class JournalEntry:
    op: str  # "move" | "copy" | "trash" | "mkdir"
    src: str
    dst: str
    status: str = "planned"  # planned | done | failed | undone
    error: Optional[str] = None


@dataclass
class Journal:
    kind: str
    label: str = ""
    id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    entries: List[JournalEntry] = field(default_factory=list)

    @staticmethod
    def journal_dir(folder: Path) -> Path:
        d = Path(folder) / CACHE_DIRNAME / JOURNAL_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def file_path(self, folder: Path) -> Path:
        return self.journal_dir(folder) / f"{self.kind}_{self.id}.json"

    def save(self, folder: Path) -> None:
        data = {
            "kind": self.kind, "label": self.label, "id": self.id,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "entries": [asdict(e) for e in self.entries],
        }
        path = self.file_path(folder)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def load(path: Path) -> "Journal":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        j = Journal(kind=data["kind"], label=data.get("label", ""), id=data["id"],
                    started_at=data["started_at"], completed_at=data.get("completed_at"))
        j.entries = [JournalEntry(**e) for e in data.get("entries", [])]
        return j

    @property
    def done_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "done")

    @property
    def undoable(self) -> bool:
        return any(e.status == "done" and e.op in ("move", "copy") for e in self.entries)


def list_journals(folder: Path) -> List[Path]:
    d = Path(folder) / CACHE_DIRNAME / JOURNAL_DIRNAME
    if not d.exists():
        return []
    return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


# ------------------------------------------------------------------ helpers

def collision_safe_path(dst: Path, reserved: Optional[set] = None) -> Path:
    """Never overwrite: if dst exists (or is reserved by this batch), add ' (n)'."""
    reserved = reserved if reserved is not None else set()

    def taken(p: Path) -> bool:
        return p.exists() or str(p).lower() in reserved

    if not taken(dst):
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m:
        stem = m.group(1)
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not taken(candidate):
            return candidate
        n += 1


def find_sidecars(src: Path) -> List[Path]:
    """Sidecars that travel with a photo: IMG_1.xmp, IMG_1.CR2.xmp, IMG_1.thm, ..."""
    out: List[Path] = []
    for ext in SIDECAR_EXTS:
        for cand in (src.with_suffix(ext), src.with_suffix(ext.upper()),
                     src.with_name(src.name + ext), src.with_name(src.name + ext.upper())):
            if cand != src and cand.exists() and cand not in out:
                out.append(cand)
    # De-duplicate case-insensitive filesystems (Windows) where both spellings "exist".
    seen, unique = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            unique.append(p)
    return unique


def _sidecar_target(sidecar: Path, src: Path, new_src: Path) -> Path:
    """Map a sidecar to its new name when the photo is moved/renamed."""
    if sidecar.name.lower().startswith(src.name.lower()):  # IMG_1.CR2.xmp style
        return new_src.with_name(new_src.name + sidecar.name[len(src.name):])
    return new_src.with_name(new_src.stem + sidecar.suffix)  # IMG_1.xmp style


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


class Transaction:
    """Batch of journaled operations. Use as a context manager."""

    def __init__(self, folder: Path, kind: str, label: str = ""):
        self.folder = Path(folder)
        self.journal = Journal(kind=kind, label=label)
        self.errors: List[str] = []
        self._reserved: set = set()
        self._sidecars_done: set = set()  # IMG_1.xmp is shared by IMG_1.CR2 + IMG_1.JPG: handle it once

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, *exc) -> None:
        self.finish()

    def finish(self) -> None:
        if self.journal.completed_at is None:
            self.journal.completed_at = time.time()
        if self.journal.entries:
            self.journal.save(self.folder)

    def _record(self, op: str, src: Path, dst: Path) -> int:
        self.journal.entries.append(JournalEntry(op=op, src=str(src), dst=str(dst)))
        if len(self.journal.entries) % 20 == 1:
            self.journal.save(self.folder)
        return len(self.journal.entries) - 1

    def _mark(self, idx: int, status: str, error: Optional[str] = None) -> None:
        self.journal.entries[idx].status = status
        self.journal.entries[idx].error = error

    def reserve(self, dst: Path) -> Path:
        safe = collision_safe_path(dst, self._reserved)
        self._reserved.add(str(safe).lower())
        return safe

    def _fresh_sidecars(self, src: Path) -> List[Path]:
        out = []
        for sc in find_sidecars(src):
            k = os.path.normcase(os.path.abspath(sc))
            if k not in self._sidecars_done:
                self._sidecars_done.add(k)
                out.append(sc)
        return out

    def move(self, src: Path, dst: Path, with_sidecars: bool = True) -> Optional[Path]:
        """Move/rename ``src`` to exactly ``dst`` (made collision-safe)."""
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            return src  # already where it should be
        safe = self.reserve(dst)
        idx = self._record("move", src, safe)
        try:
            _move_file(src, safe)
        except OSError as e:
            self._mark(idx, "failed", str(e))
            self.errors.append(f"{src.name}: {e}")
            return None
        self._mark(idx, "done")
        if with_sidecars:
            for sc in self._fresh_sidecars(src):
                sc_dst = self.reserve(_sidecar_target(sc, src, safe))
                j = self._record("move", sc, sc_dst)
                try:
                    _move_file(sc, sc_dst)
                    self._mark(j, "done")
                except OSError as e:
                    self._mark(j, "failed", str(e))
        return safe

    def copy(self, src: Path, dst: Path, with_sidecars: bool = True) -> Optional[Path]:
        safe = self.reserve(dst)
        idx = self._record("copy", src, safe)
        try:
            safe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(safe))
        except OSError as e:
            self._mark(idx, "failed", str(e))
            self.errors.append(f"{src.name}: {e}")
            return None
        self._mark(idx, "done")
        if with_sidecars:
            for sc in self._fresh_sidecars(src):
                sc_dst = self.reserve(_sidecar_target(sc, src, safe))
                j = self._record("copy", sc, sc_dst)
                try:
                    shutil.copy2(str(sc), str(sc_dst))
                    self._mark(j, "done")
                except OSError as e:
                    self._mark(j, "failed", str(e))
        return safe

    def trash(self, src: Path, with_sidecars: bool = True, fallback_dirname: str = REJECTED_DIRNAME) -> bool:
        """Recycle Bin if possible, otherwise move to ``<root>/<fallback_dirname>`` (never a hard delete)."""
        targets = [src] + (self._fresh_sidecars(src) if with_sidecars else [])
        ok_all = True
        for t in targets:
            if send_to_trash(t):
                idx = self._record("trash", t, "")
                self._mark(idx, "done")
            else:
                dst = self.folder / fallback_dirname / t.name
                if self.move(t, dst, with_sidecars=False) is None:
                    ok_all = False
        return ok_all


def send_to_trash(path: Path) -> bool:
    """Send to Recycle Bin / Trash. Returns False (and does nothing) if impossible."""
    if not path.exists():
        return False
    if _send2trash is not None:
        try:
            _send2trash(str(path))
            return True
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            op = SHFILEOPSTRUCTW(
                hwnd=None, wFunc=3, pFrom=str(path.resolve()) + "\0\0", pTo=None,
                fFlags=0x0040 | 0x0010 | 0x0004 | 0x0400,  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
                fAnyOperationsAborted=False, hNameMappings=None, lpszProgressTitle=None,
            )
            res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            return res == 0 and not op.fAnyOperationsAborted and not path.exists()
        except Exception:
            return False
    return False


def undo_journal(journal: Journal, folder: Path) -> Tuple[int, List[str]]:
    """Reverse every completed move/copy, newest first. Trash must be restored by the user."""
    restored, errors = 0, []
    for i in range(len(journal.entries) - 1, -1, -1):
        e = journal.entries[i]
        if e.status != "done":
            continue
        src, dst = Path(e.src), Path(e.dst)
        if e.op == "copy":
            try:
                if dst.exists():
                    dst.unlink()
                e.status = "undone"
                restored += 1
            except OSError as ex:
                errors.append(f"Couldn't remove copy {dst.name}: {ex}")
        elif e.op == "move":
            if not dst.exists():
                errors.append(f"{dst.name} is no longer where it was moved to")
                continue
            if src.exists():
                errors.append(f"Can't restore {src.name}: something else is already there")
                continue
            try:
                _move_file(dst, src)
                e.status = "undone"
                restored += 1
            except OSError as ex:
                errors.append(f"{dst.name}: {ex}")
        elif e.op == "trash":
            errors.append(f"{src.name} is in the Recycle Bin - restore it from there")
    _remove_empty_dirs(journal, folder)
    journal.completed_at = time.time()
    journal.save(folder)
    return restored, errors


def remove_emptied_dirs(journal: Journal, folder: Path) -> int:
    """Delete folders that this operation emptied (only those - never other empty folders)."""
    folder = Path(folder)
    dirs = set()
    for e in journal.entries:
        if e.status == "done" and e.op in ("move", "trash"):
            p = Path(e.src).parent
            while p != folder and folder in p.parents:
                dirs.add(p)
                p = p.parent
    removed = 0
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def _remove_empty_dirs(journal: Journal, folder: Path) -> None:
    """After undo, delete folders the operation created and left empty."""
    dirs = set()
    for e in journal.entries:
        if e.status == "undone" and e.dst:
            p = Path(e.dst).parent
            while p != Path(folder) and Path(folder) in p.parents:
                dirs.add(p)
                p = p.parent
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


# ------------------------------------------------------------------ XMP

XMP_LABEL_COLORS = {"Red", "Yellow", "Green", "Blue", "Purple"}

_XMP_TEMPLATE = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Photography Tools Suite">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"{attrs}/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


def xmp_path_for(image_path: Path) -> Path:
    """Lightroom / Camera Raw / Capture One / Bridge convention: IMG_0001.xmp.

    (Darktable's IMG_0001.CR2.xmp is respected if it already exists.)
    """
    lr_style = image_path.with_suffix(".xmp")
    dt_style = image_path.with_name(image_path.name + ".xmp")
    if not lr_style.exists() and dt_style.exists():
        return dt_style
    return lr_style


def read_xmp(image_path: Path) -> Tuple[Optional[int], Optional[str]]:
    """Read (rating, label) from an existing sidecar, if any."""
    for cand in (image_path.with_suffix(".xmp"), image_path.with_name(image_path.name + ".xmp")):
        if cand.exists():
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rating = label = None
            m = re.search(r'xmp:Rating\s*=\s*"(-?\d+)"', text) or re.search(r"<xmp:Rating>(-?\d+)</xmp:Rating>", text)
            if m:
                rating = int(m.group(1))
            m = re.search(r'xmp:Label\s*=\s*"([^"]*)"', text) or re.search(r"<xmp:Label>([^<]*)</xmp:Label>", text)
            if m:
                label = m.group(1) or None
            return rating, label
    return None, None


def _set_attr(text: str, name: str, value: Optional[str]) -> str:
    """Set/replace/remove an xmp: attribute or element inside an XMP packet."""
    attr_re = re.compile(rf'\s+xmp:{name}\s*=\s*"[^"]*"')
    elem_re = re.compile(rf"\s*<xmp:{name}>[^<]*</xmp:{name}>")
    if value is None:
        return elem_re.sub("", attr_re.sub("", text))
    if attr_re.search(text):
        return attr_re.sub(f' xmp:{name}="{value}"', text, count=1)
    if elem_re.search(text):
        return elem_re.sub(f"<xmp:{name}>{value}</xmp:{name}>", text, count=1)
    # Insert as an attribute on the first rdf:Description, declaring the namespace if needed.
    m = re.search(r"<rdf:Description\b", text)
    if not m:
        raise ValueError("no rdf:Description")
    insert_at = m.end()
    extra = ""
    if 'xmlns:xmp="http://ns.adobe.com/xap/1.0/"' not in text:
        extra += ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    extra += f' xmp:{name}="{value}"'
    return text[:insert_at] + extra + text[insert_at:]


def write_xmp(image_path: Path, rating: Optional[int], label: Optional[str]) -> Path:
    """Create or update the photo's sidecar with rating (-1 = rejected, 0-5) and colour label.

    Existing sidecar content (develop settings, keywords, ...) is preserved.
    """
    xmp = xmp_path_for(image_path)
    rating_s = None if rating is None else str(int(rating))
    label_s = label if label else None
    if xmp.exists():
        text = xmp.read_text(encoding="utf-8", errors="ignore")
        try:
            new = _set_attr(text, "Rating", rating_s)
            new = _set_attr(new, "Label", label_s)
            tmp = xmp.with_suffix(".xmp.tmp")
            tmp.write_text(new, encoding="utf-8")
            os.replace(tmp, xmp)
            return xmp
        except ValueError:
            backup = collision_safe_path(xmp.with_suffix(".xmp.bak"))
            shutil.copy2(str(xmp), str(backup))
    attrs = ""
    if rating_s is not None:
        attrs += f'\n    xmp:Rating="{rating_s}"'
    if label_s:
        attrs += f'\n    xmp:Label="{label_s}"'
    xmp.write_text(_XMP_TEMPLATE.format(attrs=attrs), encoding="utf-8")
    return xmp


def open_in_file_manager(path: Path) -> None:
    import subprocess

    path = Path(path)
    try:
        if sys.platform == "win32":
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except Exception:
        pass


def open_with_default_app(path: Path) -> None:
    import subprocess

    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass
