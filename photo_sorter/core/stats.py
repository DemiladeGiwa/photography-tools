"""Shoot insights: keeper rate, gear and settings breakdowns, timeline, quality spread."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .library import FLAG_PICK, FLAG_REJECT, Photo


@dataclass
class Bucket:
    label: str
    count: int = 0
    picks: int = 0
    quality_sum: float = 0.0
    quality_n: int = 0

    @property
    def keeper_rate(self) -> float:
        return self.picks / self.count if self.count else 0.0

    @property
    def avg_quality(self) -> Optional[float]:
        return self.quality_sum / self.quality_n if self.quality_n else None


@dataclass
class ShootStats:
    total: int = 0
    analysed: int = 0
    picks: int = 0
    rejects: int = 0
    rated: int = 0
    blurry: int = 0
    groups: int = 0
    in_groups: int = 0
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    avg_quality: Optional[float] = None
    timeline: List[Bucket] = field(default_factory=list)
    cameras: List[Bucket] = field(default_factory=list)
    lenses: List[Bucket] = field(default_factory=list)
    focal: List[Bucket] = field(default_factory=list)
    iso: List[Bucket] = field(default_factory=list)
    aperture: List[Bucket] = field(default_factory=list)
    shutter: List[Bucket] = field(default_factory=list)
    quality_hist: List[Bucket] = field(default_factory=list)
    sharpness_hist: List[Bucket] = field(default_factory=list)

    @property
    def keeper_rate(self) -> float:
        return self.picks / self.total if self.total else 0.0


def _add(buckets: "OrderedDict[str, Bucket]", label: str, p: Photo) -> None:
    b = buckets.get(label)
    if b is None:
        b = buckets[label] = Bucket(label)
    b.count += 1
    if p.flag == FLAG_PICK:
        b.picks += 1
    if p.quality:
        b.quality_sum += p.quality_score
        b.quality_n += 1


def _bin(value: Optional[float], edges: Sequence[float], labels: Sequence[str]) -> Optional[str]:
    if value is None:
        return None
    for edge, label in zip(edges, labels):
        if value <= edge:
            return label
    return labels[-1]


FOCAL_EDGES = [20, 28, 40, 60, 90, 140, 250, 10**6]
FOCAL_LABELS = ["≤20mm", "21-28", "29-40", "41-60", "61-90", "91-140", "141-250", "250+"]
ISO_EDGES = [200, 400, 800, 1600, 3200, 6400, 12800, 10**9]
ISO_LABELS = ["≤200", "400", "800", "1600", "3200", "6400", "12800", "12800+"]
AP_EDGES = [1.8, 2.8, 4.0, 5.6, 8.0, 11.0, 10**3]
AP_LABELS = ["≤f/1.8", "f/2-2.8", "f/3.2-4", "f/4.5-5.6", "f/6.3-8", "f/9-11", "f/13+"]
SH_EDGES = [1 / 2000, 1 / 500, 1 / 125, 1 / 30, 1 / 4, 10**6]
SH_LABELS = ["≤1/2000", "1/1000-1/500", "1/250-1/125", "1/60-1/30", "1/15-1/4", "slower"]


def _shutter_seconds(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        s = s.rstrip("s")
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def compute(photos: Sequence[Photo], groups: int = 0) -> ShootStats:
    st = ShootStats(total=len(photos), groups=groups)
    q = []
    timeline: "OrderedDict[str, Bucket]" = OrderedDict()
    cams: "OrderedDict[str, Bucket]" = OrderedDict()
    lenses: "OrderedDict[str, Bucket]" = OrderedDict()
    focal = OrderedDict((l, Bucket(l)) for l in FOCAL_LABELS)
    iso = OrderedDict((l, Bucket(l)) for l in ISO_LABELS)
    ap = OrderedDict((l, Bucket(l)) for l in AP_LABELS)
    sh = OrderedDict((l, Bucket(l)) for l in SH_LABELS)
    qh = OrderedDict((f"{i * 10}-{i * 10 + 9}", Bucket(f"{i * 10}")) for i in range(10))
    sharp = OrderedDict((f"{i * 10}", Bucket(f"{i * 10}")) for i in range(10))

    times = sorted(p.capture_time for p in photos if p.capture_time)
    span_hours = ((times[-1] - times[0]).total_seconds() / 3600) if len(times) > 1 else 0
    for p in sorted(photos, key=lambda p: (p.capture_time or datetime.max)):
        if p.analyzed:
            st.analysed += 1
        if p.flag == FLAG_PICK:
            st.picks += 1
        elif p.flag == FLAG_REJECT:
            st.rejects += 1
        if p.rating:
            st.rated += 1
        if p.is_blurry:
            st.blurry += 1
        if p.group_id:
            st.in_groups += 1
        if p.quality:
            q.append(p.quality_score)
            _add(qh, list(qh.keys())[min(9, int(p.quality_score // 10))], p)
            _add(sharp, list(sharp.keys())[min(9, int(p.sharpness_score // 10))], p)
        if p.capture_time:
            if span_hours > 72:
                label = p.capture_time.strftime("%b %d")
            elif span_hours > 3:
                label = p.capture_time.strftime("%a %H:00")
            else:
                label = p.capture_time.strftime("%H:%M")[:-1] + "0"
            _add(timeline, label, p)
        md = p.meta
        if md.model:
            _add(cams, md.model.strip(), p)
        if md.lens:
            _add(lenses, md.lens.strip(), p)
        lab = _bin(md.focal_length, FOCAL_EDGES, FOCAL_LABELS)
        if lab:
            _add(focal, lab, p)
        lab = _bin(md.iso, ISO_EDGES, ISO_LABELS)
        if lab:
            _add(iso, lab, p)
        lab = _bin(md.f_number, AP_EDGES, AP_LABELS)
        if lab:
            _add(ap, lab, p)
        lab = _bin(_shutter_seconds(md.exposure_time), SH_EDGES, SH_LABELS)
        if lab:
            _add(sh, lab, p)
    if times:
        st.first, st.last = times[0], times[-1]
    st.avg_quality = sum(q) / len(q) if q else None
    st.timeline = list(timeline.values())
    st.cameras = sorted(cams.values(), key=lambda b: -b.count)[:8]
    st.lenses = sorted(lenses.values(), key=lambda b: -b.count)[:8]
    st.focal = [b for b in focal.values()]
    st.iso = [b for b in iso.values()]
    st.aperture = [b for b in ap.values()]
    st.shutter = [b for b in sh.values()]
    st.quality_hist = list(qh.values())
    st.sharpness_hist = list(sharp.values())
    return st
