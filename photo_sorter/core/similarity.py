"""
Scalable visual-similarity candidate generation.

The old grouper compared every photo's hash against every other photo's
hash — O(n^2). At 10,000 photos that's ~50 million hamming-distance
calls just for the cheap stage, before any of the more expensive
comparisons. This module replaces that with a **BK-tree**, a metric-space
index built for exactly this problem: "find all items within distance d
of X" in a discrete metric space (Hamming distance over hash bits
qualifies) without touching every other item.

Complexity: BK-tree lookups are typically O(log n) to O(n^(1-*)) depending
on data distribution, and in the worst case degrade to O(n) — but even
that worst case is still one linear pass per query, not n^2 total, and in
practice near-duplicate photo hashes cluster tightly, which is exactly
where BK-trees excel.

A second, *stronger* comparison (color histogram correlation) is provided
for resolving ambiguous candidates — it should only ever be run on the
short candidate list a BK-tree query returns, never on all pairs.
"""

from __future__ import annotations

from typing import Callable, Generic, Hashable, List, Tuple, TypeVar

import cv2
import numpy as np

from .hashing import hamming

T = TypeVar("T", bound=Hashable)


class BKTree(Generic[T]):
    """BK-tree over integer hashes with Hamming distance as the metric."""

    __slots__ = ("_distance", "_root")

    def __init__(self, distance: Callable[[int, int], int] = hamming):
        self._distance = distance
        self._root = None  # (hash_value, item, children: dict[dist, node])

    def add(self, hash_value: int, item: T) -> None:
        if self._root is None:
            self._root = [hash_value, [item], {}]
            return
        node = self._root
        while True:
            d = self._distance(hash_value, node[0])
            if d == 0:
                node[1].append(item)
                return
            child = node[2].get(d)
            if child is None:
                node[2][d] = [hash_value, [item], {}]
                return
            node = child

    def query(self, hash_value: int, max_distance: int) -> List[Tuple[int, T]]:
        """Return (distance, item) pairs within ``max_distance``."""
        results: List[Tuple[int, T]] = []
        if self._root is None:
            return results
        stack = [self._root]
        while stack:
            node = stack.pop()
            d = self._distance(hash_value, node[0])
            if d <= max_distance:
                results.extend((d, item) for item in node[1])
            lo, hi = d - max_distance, d + max_distance
            for child_dist, child in node[2].items():
                if lo <= child_dist <= hi:
                    stack.append(child)
        return results


def histogram_signature(bgr: np.ndarray) -> np.ndarray:
    """Cheap coarse color signature used both as a candidate-bucket key and
    as an input to the stronger histogram-correlation comparison."""
    hist = cv2.calcHist([bgr], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    cv2.normalize(hist, hist)
    return hist


def histogram_similarity(hist_a: np.ndarray, hist_b: np.ndarray) -> float:
    """Correlation in [-1, 1]; 1.0 = identical color distribution."""
    return float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))


def hist_to_list(hist: np.ndarray) -> List[float]:
    """Convert normalized histogram array to a float list for JSON caching."""
    return [float(v) for v in hist.flatten()]



def list_to_hist(lst: List[float]) -> np.ndarray:
    """Convert cached float list back to OpenCV histogram array."""
    return np.array(lst, dtype=np.float32).reshape((8, 8, 8))


def normalized_pixel_similarity(gray_a: np.ndarray, gray_b: np.ndarray, size: int = 64) -> float:
    """Cheap structural-similarity stand-in: resize both to a small common
    size and take normalized cross-correlation. Not a substitute for real
    SSIM, but far cheaper and enough to disambiguate "same color palette,
    different scene" from "actually the same shot" among a short
    candidate list."""
    a = cv2.resize(gray_a, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    b = cv2.resize(gray_b, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.sum(a * b) / denom)

