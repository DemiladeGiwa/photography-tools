"""
The photo library: the one shared model every tool works on.

* **Discovery** - finds photos (optionally in subfolders) and pairs RAW+JPEG
  files with the same name into a single photo, so a decision applies to
  both files and they always move together.
* **Analysis** - attaches cached single-pass analysis (see ``analysis.py``).
* **Decisions** - star rating, pick/reject flag and colour label per photo,
  autosaved to ``.photo_sorter_cache/library_state.json`` keyed by a stable
  id (file name + size) so they survive moves and re-organising. Ratings and
  labels already in XMP sidecars are imported on first open.
* **Derived data** - quality scores, blur verdicts and groups, recomputed
  cheaply from cached signals whenever settings change.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import analysis as an
from . import blur_detector, exposure, grouping, scoring
from .cache import SignatureCache
from .constants import (ALL_EXTS, CACHE_DIRNAME, RAW_EXTS, SKIP_DIR_NAMES, STATE_FILE_NAME)
from .file_operations import read_xmp
from .metadata import PhotoMetadata

FLAG_PICK = "pick"
FLAG_REJECT = "reject"

# Preferred display/analysis file when a RAW has a companion: the camera JPEG
# is faster to decode and shows the in-camera rendering.
_DISPLAY_PREF = [".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".png", ".webp", ".bmp"]


@dataclass
class ScoringSettings:
    sharpness_weight: float = 60.0
    exposure_weight: float = 40.0
    focus_profile: str = "balanced"
    blur_threshold: float = 55.0  # 0-100 sharpness below which a photo counts as blurry
    group_sensitivity: str = "Balanced"
    group_time_window: float = 90.0
    group_across_shoot: bool = True

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_json(d: dict) -> "ScoringSettings":
        s = ScoringSettings()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass(eq=False)
class Photo:
    key: str
    master: Path  # RAW if the photo has one, else the only file
    files: List[Path]  # every image file belonging to this photo
    display: Path  # file used for viewing/analysis
    rel_dir: str = ""
    size_bytes: int = 0
    mtime: float = 0.0

    data: Optional[dict] = None
    error: Optional[str] = None
    meta: PhotoMetadata = field(default_factory=PhotoMetadata)
    capture_time: Optional[datetime] = None
    time_from_exif: bool = False

    quality: Optional[exposure.QualityResult] = None
    blur: Optional[blur_detector.BlurResult] = None

    rating: int = 0
    flag: Optional[str] = None
    label: Optional[str] = None
    touched: bool = False  # user made a decision by hand (auto-cull won't override)

    group_id: Optional[int] = None
    is_best: bool = False
    best_override: bool = False

    # ---- convenience
    @property
    def name(self) -> str:
        return self.master.name

    @property
    def stem(self) -> str:
        return self.master.stem

    @property
    def analyzed(self) -> bool:
        return self.data is not None and self.error is None

    @property
    def has_raw(self) -> bool:
        return any(f.suffix.lower() in RAW_EXTS for f in self.files)

    @property
    def type_label(self) -> str:
        exts = []
        for f in self.files:
            e = f.suffix.lower().lstrip(".")
            e = "JPG" if e in ("jpg", "jpeg") else ("RAW" if f.suffix.lower() in RAW_EXTS else e.upper())
            if e not in exts:
                exts.append(e)
        exts.sort(key=lambda e: (e != "RAW", e))
        return "+".join(exts)

    @property
    def quality_score(self) -> float:
        return self.quality.quality_score if self.quality else 0.0

    @property
    def sharpness_score(self) -> float:
        return self.quality.sharpness_score if self.quality else 0.0

    @property
    def exposure_score(self) -> float:
        return self.quality.exposure_score if self.quality else 0.0

    @property
    def is_blurry(self) -> bool:
        return bool(self.blur and self.blur.is_blurry)

    @property
    def composite_sharpness(self) -> float:
        if self.data and self.data.get("blur"):
            return float(self.data["blur"].get("composite_score", 0.0))
        return 0.0

    @property
    def aspect(self) -> float:
        if self.data and self.data.get("aw") and self.data.get("ah"):
            return self.data["aw"] / max(1, self.data["ah"])
        return 1.5


def _stable_key(master: Path, size: int) -> str:
    return f"{master.name.lower()}|{size}"


def _iter_image_files(root: Path, recursive: bool) -> Iterable[Path]:
    if not recursive:
        try:
            for entry in os.scandir(root):
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in ALL_EXTS:
                    yield Path(entry.path)
        except OSError:
            return
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith("."))
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in ALL_EXTS and not fn.startswith("._"):
                yield Path(dirpath) / fn


def discover(root: Path, recursive: bool = False) -> List[Photo]:
    """Find photos and pair same-name RAW + JPEG/HEIC/TIFF files into one photo."""
    root = Path(root)
    by_dir_stem: Dict[tuple, List[Path]] = {}
    for f in _iter_image_files(root, recursive):
        by_dir_stem.setdefault((str(f.parent), f.stem.lower()), []).append(f)

    photos: List[Photo] = []
    for (_d, _stem), files in by_dir_stem.items():
        raws = [f for f in files if f.suffix.lower() in RAW_EXTS]
        others = [f for f in files if f.suffix.lower() not in RAW_EXTS]
        units: List[List[Path]] = []
        if len(raws) == 1 and others:
            units.append([raws[0]] + others)
        else:
            units.extend([f] for f in files)
        for unit in units:
            master = next((f for f in unit if f.suffix.lower() in RAW_EXTS), unit[0])
            display = master
            if len(unit) > 1:
                for ext in _DISPLAY_PREF:
                    hit = next((f for f in unit if f.suffix.lower() == ext), None)
                    if hit is not None:
                        display = hit
                        break
            try:
                st = master.stat()
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                size, mtime = 0, 0.0
            try:
                rel = str(master.parent.relative_to(root))
            except ValueError:
                rel = ""
            photos.append(Photo(key=_stable_key(master, size), master=master,
                                files=sorted(unit, key=lambda f: (f != master, f.suffix.lower())),
                                display=display, rel_dir="" if rel == "." else rel,
                                size_bytes=sum(_safe_size(f) for f in unit), mtime=mtime))
    # Ensure keys are unique even for same-name/same-size files in different folders.
    seen: Dict[str, int] = {}
    for p in photos:
        if p.key in seen:
            seen[p.key] += 1
            p.key = f"{p.key}|{p.rel_dir}|{seen[p.key]}"
        else:
            seen[p.key] = 0
    photos.sort(key=lambda p: (p.rel_dir.lower(), p.name.lower()))
    return photos


def _safe_size(f: Path) -> int:
    try:
        return f.stat().st_size
    except OSError:
        return 0


class Library:
    """A folder of photos plus everything known about them."""

    def __init__(self, root: Path, recursive: bool = False, use_cache: bool = True):
        self.root = Path(root)
        self.recursive = recursive
        self.photos: List[Photo] = []
        self.by_key: Dict[str, Photo] = {}
        self.groups: List[grouping.Group] = []
        self.groups_by_id: Dict[int, grouping.Group] = {}
        self.settings = ScoringSettings()
        self.cache: Optional[SignatureCache] = SignatureCache(self.root) if use_cache else None
        self._dirty = False
        self._lock = threading.RLock()
        self.last_run: Optional[an.AnalysisRun] = None
        self._saved_state: Dict[str, dict] = {}

    # ------------------------------------------------------------ loading
    @property
    def state_path(self) -> Path:
        return self.root / CACHE_DIRNAME / STATE_FILE_NAME

    def scan(self) -> None:
        """Discover files and restore saved decisions (fast; no decoding)."""
        self._saved_state = self._read_state()
        photos = discover(self.root, self.recursive)
        decisions = self._saved_state.get("decisions", {})
        self.settings = ScoringSettings.from_json(self._saved_state.get("settings", {}))
        cached = self.cache.get_many(p.display for p in photos) if self.cache is not None else {}
        for p in photos:
            d = decisions.get(p.key)
            if d:
                p.rating = int(d.get("r", 0))
                p.flag = d.get("f")
                p.label = d.get("l")
                p.touched = bool(d.get("t", False))
                p.best_override = bool(d.get("b", False))
            else:
                self._import_xmp(p)
            # cached analysis available immediately -> instant reopen
            hit = cached.get(str(p.display))
            if hit and hit.get("version") == an.ANALYSIS_VERSION and "error" not in hit:
                self._attach(p, hit)
        with self._lock:
            self.photos = photos
            self.by_key = {p.key: p for p in photos}
        self.recompute(regroup=False)
        if not self._restore_groups():
            self.compute_groups()

    def _import_xmp(self, p: Photo) -> None:
        for f in p.files:
            rating, label = read_xmp(f)
            if rating is not None or label:
                if rating is not None and rating < 0:
                    p.flag = FLAG_REJECT
                elif rating is not None:
                    p.rating = max(0, min(5, rating))
                if label:
                    p.label = label
                return

    def pending_paths(self) -> List[Photo]:
        return [p for p in self.photos if p.data is None]

    def analyze(self, progress_cb=None, should_stop=lambda: False, on_photo: Optional[Callable[[Photo], None]] = None,
                force: bool = False) -> an.AnalysisRun:
        """Analyse photos that have no cached result (or all, with ``force``)."""
        todo = self.photos if force else [p for p in self.photos if p.data is None or p.error]
        if force and self.cache is not None:
            self.cache.clear()
        by_display = {}
        for p in todo:
            by_display[str(p.display)] = p

        def on_result(path: Path, data: dict) -> None:
            p = by_display.get(str(path))
            if p is None:
                return
            self._attach(p, data)
            self._score_one(p)
            if on_photo:
                on_photo(p)

        run = an.analyze_many([p.display for p in todo], self.cache, on_result, progress_cb, should_stop)
        self.last_run = run
        self.recompute(regroup=True)
        return run

    def _attach(self, p: Photo, data: dict) -> None:
        p.__dict__.pop("_gi", None)
        p.data = data if "error" not in data else None
        p.error = data.get("error")
        md = PhotoMetadata.from_json(data.get("meta") or {})
        p.meta = md
        dt = md.dt()
        p.time_from_exif = dt is not None
        if dt is None:
            try:
                dt = datetime.fromtimestamp(p.master.stat().st_mtime)
            except OSError:
                dt = None
        p.capture_time = dt

    # ------------------------------------------------------------ derived data
    def _score_one(self, p: Photo) -> None:
        if not p.analyzed:
            p.quality, p.blur = None, None
            return
        s = self.settings
        b = p.data.get("blur")
        p.quality = scoring.compute_quality(
            b, p.data.get("exposure"),
            sharpness_weight=s.sharpness_weight, exposure_weight=s.exposure_weight,
            focus_profile=s.focus_profile,
        )
        p.blur = blur_detector.classify(b, s.blur_threshold, s.focus_profile) if b else None

    def recompute(self, regroup: bool = True) -> None:
        for p in self.photos:
            self._score_one(p)
        if regroup:
            self.compute_groups()

    def compute_groups(self) -> None:
        s = self.settings
        items = [self._group_input(p) for p in self.photos if p.analyzed]
        groups = grouping.compute_groups(items, s.group_sensitivity, s.group_time_window, s.group_across_shoot)
        for p in self.photos:
            p.group_id, p.is_best = None, False
        for g in groups:
            override = next((k for k in g.members if self.by_key[k].best_override), None)
            if override:
                g.best = override
            for k in g.members:
                ph = self.by_key[k]
                ph.group_id = g.id
                ph.is_best = (k == g.best)
        with self._lock:
            self.groups = groups
            self.groups_by_id = {g.id: g for g in groups}
        self._dirty = True

    @staticmethod
    def _group_input(p: "Photo") -> grouping.GroupInput:
        d = p.data
        return grouping.GroupInput(
            key=p.key, name=p.name, time=p.capture_time if p.time_from_exif else None,
            phash=d.get("phash"), dhash=d.get("dhash"), ephash=d.get("ephash"), edhash=d.get("edhash"),
            tiny=an.tiny_from_b64(d.get("tiny")), hist=an.hist_from_b64(d.get("hist")),
            sharpness=p.composite_sharpness, exposure=p.exposure_score, quality=p.quality_score,
        )

    def find_similar(self, target: "Photo", limit: int = 80, min_similarity: float = 0.42) -> List[tuple]:
        """Photos that look like ``target`` anywhere in the folder, most similar first: [(similarity, photo)]."""
        if not target.analyzed:
            return []
        t = self._group_input(target)
        t.time = None  # visual similarity only - ignore when it was shot (no burst bonus)
        out = []
        for p in self.photos:
            if p is target or not p.analyzed:
                continue
            gi = p.__dict__.get("_gi")
            if gi is None or gi.key != p.key:
                gi = self._group_input(p)
                p.__dict__["_gi"] = gi  # cached (keeps its prepared vectors) for the next search
            sim, _parts = grouping.pair_similarity(t, gi)
            if sim >= min_similarity:
                out.append((sim, p))
        out.sort(key=lambda x: -x[0])
        return out[:limit]

    def _groups_signature(self) -> str:
        import hashlib

        s = self.settings
        h = hashlib.blake2b(digest_size=12)
        h.update(f"{an.ANALYSIS_VERSION}|{s.group_sensitivity}|{s.group_time_window}|{s.group_across_shoot}".encode())
        for k in sorted(p.key for p in self.photos if p.analyzed):
            h.update(k.encode())
        return h.hexdigest()

    def _restore_groups(self) -> bool:
        """Reuse groups saved last session if the photos and settings haven't changed."""
        saved = self._saved_state.get("groups")
        if saved is None or self._saved_state.get("groups_sig") != self._groups_signature():
            return False
        groups = []
        for d in saved:
            members = [k for k in d.get("members", []) if k in self.by_key]
            if len(members) < 2:
                continue
            groups.append(grouping.Group(id=d["id"], kind=d.get("kind", "similar"), members=members,
                                         best=d.get("best") if d.get("best") in members else members[0],
                                         cohesion=d.get("cohesion", 0.0)))
        for p in self.photos:
            p.group_id, p.is_best = None, False
        for g in groups:
            override = next((k for k in g.members if self.by_key[k].best_override), None)
            if override:
                g.best = override
            for k in g.members:
                self.by_key[k].group_id = g.id
                self.by_key[k].is_best = k == g.best
        with self._lock:
            self.groups = groups
            self.groups_by_id = {g.id: g for g in groups}
        return True

    def set_group_best(self, key: str) -> None:
        p = self.by_key[key]
        g = self.groups_by_id.get(p.group_id or -1)
        if not g:
            return
        for k in g.members:
            m = self.by_key[k]
            m.best_override = (k == key)
            m.is_best = (k == key)
        g.best = key
        self.mark_dirty()

    def remove_from_group(self, key: str) -> None:
        p = self.by_key[key]
        g = self.groups_by_id.get(p.group_id or -1)
        if not g:
            return
        g.members.remove(key)
        p.group_id, p.is_best, p.best_override = None, False, False
        if len(g.members) < 2:
            for k in g.members:
                self.by_key[k].group_id = None
                self.by_key[k].is_best = False
            self.groups.remove(g)
            del self.groups_by_id[g.id]
        elif g.best == key:
            best = max(g.members, key=lambda k: self.by_key[k].quality_score)
            g.best = best
            self.by_key[best].is_best = True
        self.mark_dirty()  # saved groups are restored on reopen, so the edit must be saved too

    def group_members(self, gid: int) -> List[Photo]:
        g = self.groups_by_id.get(gid)
        return [self.by_key[k] for k in g.members if k in self.by_key] if g else []

    # ------------------------------------------------------------ decisions
    def set_flag(self, photos: Sequence[Photo], flag: Optional[str]) -> None:
        for p in photos:
            p.flag = flag
            p.touched = True
        self.mark_dirty()

    def set_rating(self, photos: Sequence[Photo], rating: int) -> None:
        for p in photos:
            p.rating = max(0, min(5, int(rating)))
            p.touched = True
        self.mark_dirty()

    def set_label(self, photos: Sequence[Photo], label: Optional[str]) -> None:
        toggle_off = label is not None and all(p.label == label for p in photos)
        for p in photos:
            p.label = None if (label is None or toggle_off) else label
            p.touched = True
        self.mark_dirty()

    def clear_decisions(self, photos: Optional[Sequence[Photo]] = None) -> None:
        for p in photos if photos is not None else self.photos:
            p.flag, p.rating, p.label, p.touched = None, 0, None, False
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _read_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, force: bool = False) -> None:
        if not (self._dirty or force):
            return
        decisions = dict(self._saved_state.get("decisions", {}))  # keep entries for files not present now
        for p in self.photos:
            if p.flag or p.rating or p.label or p.touched or p.best_override:
                decisions[p.key] = {"r": p.rating, "f": p.flag, "l": p.label, "t": p.touched, "b": p.best_override}
            else:
                decisions.pop(p.key, None)
        state = {"version": 1, "saved_at": time.time(), "decisions": decisions,
                 "settings": self.settings.to_json(), "recursive": self.recursive,
                 "groups": [g.to_json() for g in self.groups], "groups_sig": self._groups_signature()}
        if self._write_state(state):
            self._saved_state = state
            self._dirty = False

    def _write_state(self, state: dict) -> bool:
        """Write via a temp file + rename so a crash or full disk never leaves a half-written state file."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self.state_path)
            return True
        except OSError:
            return False

    def migrate_keys(self, journal, reverse: bool = False) -> int:
        """Carry decisions (and cached analysis) over to files that were moved/renamed by ``journal``.

        Decisions are keyed by file name + size, so a pure move keeps them anyway;
        this handles renames. ``reverse`` is used after an undo.
        """
        self.save_state(force=True)
        decisions = self._saved_state.setdefault("decisions", {})
        moved = 0
        for e in journal.entries:
            if e.op != "move":
                continue
            if reverse:
                if e.status != "undone":
                    continue
                old, new = Path(e.dst), Path(e.src)
            else:
                if e.status != "done":
                    continue
                old, new = Path(e.src), Path(e.dst)
            if old.suffix.lower() not in ALL_EXTS:
                continue
            try:
                size = new.stat().st_size
            except OSError:
                continue
            if self.cache is not None and old.name != new.name:
                self.cache.rename(old.name, new)
            old_key, new_key = f"{old.name.lower()}|{size}", f"{new.name.lower()}|{size}"
            if old_key != new_key and old_key in decisions:
                decisions[new_key] = decisions.pop(old_key)
                moved += 1
        self._write_state(self._saved_state)
        return moved

    def close(self) -> None:
        self.save_state()
        if self.cache is not None:
            self.cache.close()

    # ------------------------------------------------------------ queries
    def analyzed_count(self) -> int:
        return sum(1 for p in self.photos if p.analyzed)

    def counts(self) -> Dict[str, int]:
        c = {"total": len(self.photos), "picks": 0, "rejects": 0, "unflagged": 0, "rated": 0,
             "blurry": 0, "groups": len(self.groups), "errors": 0, "analyzed": 0}
        for p in self.photos:
            if p.flag == FLAG_PICK:
                c["picks"] += 1
            elif p.flag == FLAG_REJECT:
                c["rejects"] += 1
            else:
                c["unflagged"] += 1
            if p.rating:
                c["rated"] += 1
            if p.is_blurry:
                c["blurry"] += 1
            if p.error:
                c["errors"] += 1
            if p.analyzed:
                c["analyzed"] += 1
        return c

    def thumbnail_bytes(self, p: Photo) -> Optional[bytes]:
        return self.cache.get_thumbnail(p.display) if self.cache is not None else None


# ---------------------------------------------------------------- filtering

@dataclass
class PhotoFilter:
    flag: str = "all"  # all | picks | rejects | unflagged | not_rejected
    min_rating: int = 0
    label: Optional[str] = None
    only_blurry: bool = False
    only_grouped: bool = False
    only_best: bool = False
    hide_non_best: bool = False
    min_quality: float = 0.0
    text: str = ""
    folder: Optional[str] = None

    def matches(self, p: Photo) -> bool:
        if self.flag == "picks" and p.flag != FLAG_PICK:
            return False
        if self.flag == "rejects" and p.flag != FLAG_REJECT:
            return False
        if self.flag == "unflagged" and p.flag is not None:
            return False
        if self.flag == "not_rejected" and p.flag == FLAG_REJECT:
            return False
        if self.min_rating and p.rating < self.min_rating:
            return False
        if self.label and p.label != self.label:
            return False
        if self.only_blurry and not p.is_blurry:
            return False
        if self.only_grouped and p.group_id is None:
            return False
        if self.only_best and not p.is_best:
            return False
        if self.hide_non_best and p.group_id is not None and not p.is_best:
            return False
        if self.min_quality and p.quality_score < self.min_quality:
            return False
        if self.folder is not None and p.rel_dir != self.folder:
            return False
        if self.text:
            t = self.text.lower()
            hay = f"{p.name} {p.rel_dir} {p.meta.model or ''} {p.meta.lens or ''}".lower()
            if t not in hay:
                return False
        return True


SORT_KEYS: Dict[str, Callable[[Photo], object]] = {
    "Capture time": lambda p: (p.capture_time or datetime.max, p.name.lower()),
    "File name": lambda p: (p.rel_dir.lower(), p.name.lower()),
    "Quality (best first)": lambda p: -p.quality_score,
    "Sharpness (best first)": lambda p: -p.sharpness_score,
    "Exposure (best first)": lambda p: -p.exposure_score,
    "Rating (high first)": lambda p: (-p.rating, p.capture_time or datetime.max),
    "Group": lambda p: (p.group_id is None, p.group_id or 0, not p.is_best, p.capture_time or datetime.max),
}


def filter_and_sort(photos: Sequence[Photo], flt: PhotoFilter, sort_key: str = "Capture time") -> List[Photo]:
    out = [p for p in photos if flt.matches(p)]
    out.sort(key=SORT_KEYS.get(sort_key, SORT_KEYS["Capture time"]))
    return out
