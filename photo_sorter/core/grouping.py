"""
Grouping engine: bursts, similar shots and duplicates.

How it works
------------
1. **Candidate pairs** (never all-pairs):
   * photos sorted by capture time; each photo is compared with the next few
     photos taken within ``time_window`` seconds;
   * a BK-tree over pHashes finds near-identical frames anywhere in the shoot
     (re-shots, duplicates, exports of the same frame);
   * photos without a timestamp fall back to filename-sequence neighbours.
2. **Similarity** of a pair blends three independent signals so that no one
   failure mode dominates:
   * perceptual hashes (pHash + dHash) - robust to blur and exposure shifts;
   * normalised cross-correlation of a tiny grey thumbnail - robust to
     brightness changes, sensitive to composition;
   * colour-histogram correlation - catches same-scene/different-framing.
   A small bonus is given to frames shot within a burst gap of each other.
3. **Average-linkage clustering**: pairs are merged strongest-first, but two
   clusters only merge if the *average* similarity across all their members
   stays above the threshold. This stops "chains" (A~B~C~D) from gluing
   unrelated scenes together.
4. Each group gets a **best shot**: within a group the frames share content,
   so raw sharpness is directly comparable and weighs heavily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .hashing import hamming
from .similarity import BKTree

SENSITIVITY_THRESHOLDS = {
    "Strict": 0.80,
    "Balanced": 0.70,
    "Loose": 0.60,
}

BURST_GAP_SECONDS = 2.0
DEFAULT_TIME_WINDOW = 90.0
MAX_NEIGHBOURS = 14
NEAR_DUP_HASH_DISTANCE = 10
MAX_GROUP_SIZE = 80


@dataclass
class GroupInput:
    """Minimal per-photo data the grouping engine needs (keeps it testable)."""

    key: str
    name: str
    time: Optional[datetime] = None
    phash: Optional[int] = None
    dhash: Optional[int] = None
    ephash: Optional[int] = None  # hashes of the histogram-equalised image (exposure-robust)
    edhash: Optional[int] = None
    tiny: Optional[np.ndarray] = None
    hist: Optional[np.ndarray] = None
    sharpness: float = 0.0  # raw composite sharpness
    exposure: float = 50.0  # 0-100 exposure score
    quality: float = 50.0  # 0-100 overall quality


@dataclass
class Group:
    id: int
    kind: str  # "burst" | "similar" | "duplicate"
    members: List[str]
    best: Optional[str] = None
    cohesion: float = 0.0  # average pairwise similarity, 0-1

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "members": list(self.members), "best": self.best,
                "cohesion": round(self.cohesion, 3)}


# ---------------------------------------------------------------- similarity

_SHIFTS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))


def _unit(v: np.ndarray) -> Optional[np.ndarray]:
    v = v.astype(np.float32).ravel()
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else None


def _shift_vectors(t: np.ndarray) -> Dict[Tuple[int, int], Optional[np.ndarray]]:
    """Unit vectors of the crops needed to compare against a neighbour shifted by (dx, dy)."""
    h, w = t.shape
    out = {}
    for dx, dy in _SHIFTS:
        out[(dx, dy)] = _unit(t[max(0, dy): h + min(0, dy), max(0, dx): w + min(0, dx)])
    return out


def _prep(it: "GroupInput") -> dict:
    cache = it.__dict__.get("_prep")
    if cache is None:
        cache = {}
        if it.tiny is not None:
            cache["raw"] = _shift_vectors(it.tiny)
            cache["eq"] = _shift_vectors(cv2.equalizeHist(it.tiny))
            cache["flat_mean"] = float(it.tiny.mean())
        it.__dict__["_prep"] = cache
    return cache


def _ncc_prepped(pa: dict, pb: dict, kind: str) -> float:
    """Shift-tolerant (+/-1 px) normalised cross-correlation of the tiny thumbnails."""
    A, B = pa[kind], pb[kind]
    best = -1.0
    for dx, dy in _SHIFTS:
        va, vb = A[(dx, dy)], B[(-dx, -dy)]
        if va is None or vb is None:
            if va is None and vb is None:  # two flat frames (black / lens cap)
                best = max(best, 1.0 if abs(pa["flat_mean"] - pb["flat_mean"]) < 2 else 0.0)
            continue
        best = max(best, float(np.dot(va, vb)))
        if (dx, dy) == (0, 0) and best > 0.97:
            break
    return best


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    va, vb = _unit(a), _unit(b)
    if va is None or vb is None:
        return 1.0 if va is None and vb is None and abs(float(a.mean()) - float(b.mean())) < 2 else 0.0
    return float(np.dot(va, vb))


def _hash_bits(a: GroupInput, b: GroupInput) -> Optional[float]:
    dists = []
    if a.phash is not None and b.phash is not None:
        pd = hamming(a.phash, b.phash)
        dd = hamming(a.dhash, b.dhash) if a.dhash is not None and b.dhash is not None else pd
        dists.append((pd + dd) / 2.0)
    if a.ephash is not None and b.ephash is not None:
        pd = hamming(a.ephash, b.ephash)
        dd = hamming(a.edhash, b.edhash) if a.edhash is not None and b.edhash is not None else pd
        dists.append((pd + dd) / 2.0)
    return min(dists) if dists else None


def pair_similarity(a: GroupInput, b: GroupInput) -> Tuple[float, Dict[str, float]]:
    """Return (similarity 0-1, component breakdown)."""
    parts: Dict[str, float] = {}
    weights: Dict[str, float] = {}
    bits = _hash_bits(a, b)
    if bits is not None:
        parts["hash"] = max(0.0, 1.0 - bits / 30.0)
        parts["hash_bits"] = bits
        weights["hash"] = 0.45
    dt = abs((a.time - b.time).total_seconds()) if a.time and b.time else None
    # Cheap early exit: frames whose hashes are far apart and that weren't shot
    # back-to-back can't reach any threshold, so skip the structural comparison.
    if bits is not None and bits >= 26 and (dt is None or dt > BURST_GAP_SECONDS):
        parts["structure"] = 0.0
        return 0.1, parts
    if a.tiny is not None and b.tiny is not None:
        pa, pb = _prep(a), _prep(b)
        ncc = _ncc_prepped(pa, pb, "raw")
        parts["ncc"] = ncc
        if ncc < 0.9:  # exposure-robust second opinion (brackets, flash/no-flash)
            ncc = max(ncc, _ncc_prepped(pa, pb, "eq"))
        parts["structure"] = float(np.clip((ncc - 0.25) / 0.70, 0.0, 1.0))
        weights["structure"] = 0.35
    if not weights:
        return 0.0, parts
    base_w = sum(weights.values())
    sim = sum(parts[k] * w for k, w in weights.items()) / base_w
    if a.hist is not None and b.hist is not None:
        corr = float(cv2.compareHist(a.hist, b.hist, cv2.HISTCMP_CORREL))
        parts["color"] = float(np.clip(corr, 0.0, 1.0))
        with_color = (sim * base_w + parts["color"] * 0.20) / (base_w + 0.20)
        # Colour can confirm a match but only mildly veto one: exposure changes
        # (brackets, flash) shift histograms while structure stays the same.
        sim = max(with_color, sim * 0.95)
    if dt is not None:
        parts["seconds"] = dt
        if dt <= BURST_GAP_SECONDS:
            sim += 0.10
        elif dt <= 10:
            sim += 0.04
    return float(min(1.0, sim)), parts


def is_near_identical(parts: Dict[str, float]) -> bool:
    """Same frame (exported twice, copied, re-saved) rather than a burst neighbour."""
    return (parts.get("hash_bits", 99) <= 2 and parts.get("ncc", 0) >= 0.99
            and parts.get("color", 1.0) >= 0.97 and parts.get("seconds", 0.0) == 0.0)


# ---------------------------------------------------------------- candidates

def _seq(name: str) -> Tuple[str, Optional[int]]:
    stem = name.rsplit(".", 1)[0]
    nums = re.findall(r"\d+", stem)
    if not nums:
        return stem.lower(), None
    return re.sub(r"\d+", "#", stem).lower(), int(nums[-1])


def candidate_pairs(items: Sequence[GroupInput], time_window: float = DEFAULT_TIME_WINDOW,
                    across_shoot: bool = True) -> Iterable[Tuple[int, int]]:
    seen = set()

    def emit(i: int, j: int):
        key = (i, j) if i < j else (j, i)
        if i != j and key not in seen:
            seen.add(key)
            return key
        return None

    timed = sorted((i for i, it in enumerate(items) if it.time), key=lambda i: items[i].time)
    for pos, i in enumerate(timed):
        ti = items[i].time
        for j in timed[pos + 1: pos + 1 + MAX_NEIGHBOURS]:
            if (items[j].time - ti).total_seconds() > time_window:
                break
            k = emit(i, j)
            if k:
                yield k

    # Untimed photos: filename sequence neighbours.
    buckets: Dict[str, List[Tuple[int, int]]] = {}
    for i, it in enumerate(items):
        if it.time is None:
            prefix, num = _seq(it.name)
            if num is not None:
                buckets.setdefault(prefix, []).append((num, i))
    for lst in buckets.values():
        lst.sort()
        for a in range(len(lst)):
            for b in range(a + 1, min(len(lst), a + 6)):
                if lst[b][0] - lst[a][0] > 8:
                    break
                k = emit(lst[a][1], lst[b][1])
                if k:
                    yield k

    if across_shoot:
        tree: BKTree[int] = BKTree()
        hashed = [i for i, it in enumerate(items) if it.phash is not None]
        for i in hashed:
            tree.add(items[i].phash, i)
        for i in hashed:
            for _d, j in tree.query(items[i].phash, NEAR_DUP_HASH_DISTANCE):
                k = emit(i, j)
                if k:
                    yield k


# ---------------------------------------------------------------- clustering

def best_in_group(items: Sequence[GroupInput], member_idx: Sequence[int]) -> int:
    """Pick the strongest frame. Same content => raw sharpness is comparable."""
    max_sharp = max((items[i].sharpness for i in member_idx), default=0.0) or 1.0

    def score(i: int) -> float:
        it = items[i]
        rel = it.sharpness / max_sharp
        return 0.55 * rel * 100.0 + 0.30 * it.exposure + 0.15 * it.quality

    return max(member_idx, key=score)


def compute_groups(
    items: Sequence[GroupInput],
    sensitivity: str = "Balanced",
    time_window: float = DEFAULT_TIME_WINDOW,
    across_shoot: bool = True,
    threshold: Optional[float] = None,
) -> List[Group]:
    thr = threshold if threshold is not None else SENSITIVITY_THRESHOLDS.get(sensitivity, 0.70)
    n = len(items)
    sim_cache: Dict[Tuple[int, int], Tuple[float, Dict[str, float]]] = {}

    def sim(i: int, j: int) -> float:
        key = (i, j) if i < j else (j, i)
        if key not in sim_cache:
            sim_cache[key] = pair_similarity(items[key[0]], items[key[1]])
        return sim_cache[key][0]

    scored = []
    for i, j in candidate_pairs(items, time_window, across_shoot):
        s = sim(i, j)
        if s >= thr:
            scored.append((s, i, j))
    scored.sort(reverse=True)

    cluster_of = list(range(n))
    members: Dict[int, List[int]] = {i: [i] for i in range(n)}

    for s, i, j in scored:
        ca, cb = cluster_of[i], cluster_of[j]
        if ca == cb:
            continue
        A, B = members[ca], members[cb]
        if len(A) + len(B) > MAX_GROUP_SIZE:
            continue
        cross = [(x, y) for x in A for y in B]
        if len(cross) > 300:
            step = len(cross) // 300 + 1
            cross = cross[::step]
        avg = sum(sim(x, y) for x, y in cross) / len(cross)
        if avg < thr - 0.04:
            continue
        if len(A) < len(B):
            ca, cb, A, B = cb, ca, B, A
        for m in B:
            cluster_of[m] = ca
        A.extend(B)
        del members[cb]

    groups: List[Group] = []
    order_keys: List[tuple] = []
    for root, mem in members.items():
        if len(mem) < 2:
            continue
        mem_sorted = sorted(mem, key=lambda i: (items[i].time or datetime.min, items[i].name.lower()))
        pairs = [(a, b) for k, a in enumerate(mem_sorted) for b in mem_sorted[k + 1:]]
        if len(pairs) > 200:
            pairs = pairs[:: len(pairs) // 200 + 1]
        cohesion = sum(sim(a, b) for a, b in pairs) / len(pairs)
        kind = "similar"
        if all(is_near_identical(sim_cache[(min(a, b), max(a, b))][1]) for a, b in pairs):
            kind = "duplicate"
        elif all(items[i].time for i in mem_sorted) and all(
            (items[mem_sorted[k + 1]].time - items[mem_sorted[k]].time).total_seconds() <= BURST_GAP_SECONDS
            for k in range(len(mem_sorted) - 1)
        ):
            kind = "burst"
        best = best_in_group(items, mem_sorted)
        groups.append(Group(id=0, kind=kind, members=[items[i].key for i in mem_sorted],
                            best=items[best].key, cohesion=cohesion))
        first = items[mem_sorted[0]]
        order_keys.append((first.time or datetime.max, first.name.lower()))

    order = sorted(range(len(groups)), key=lambda k: order_keys[k])
    groups = [groups[k] for k in order]
    for idx, g in enumerate(groups, start=1):
        g.id = idx
    return groups


# ---------------------------------------------------------------- moments

def split_moments(times: Sequence[Tuple[str, Optional[datetime]]], gap_minutes: float = 45.0) -> Dict[str, int]:
    """Split a shoot into 'events' wherever the capture-time gap exceeds ``gap_minutes``.

    Returns key -> 1-based event number. Untimed photos get 0.
    """
    timed = sorted(((t, k) for k, t in times if t is not None))
    out: Dict[str, int] = {k: 0 for k, t in times if t is None}
    event = 0
    last: Optional[datetime] = None
    for t, k in timed:
        if last is None or (t - last).total_seconds() > gap_minutes * 60:
            event += 1
        out[k] = event
        last = t
    return out
