"""
Single-pass photo analysis.

One decode per photo produces *everything* the suite needs — sharpness
signals, exposure signals, perceptual hashes, a colour signature, a tiny
grey thumbnail for structural comparison, metadata and the grid thumbnail —
and stores it in the SQLite cache. Every tool (culling, grouping, auto-cull,
organiser, reports) reads from that one result, so a folder is only ever
decoded once.
"""

from __future__ import annotations

import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import blur_detector, exposure, hashing, image_loader, metadata
from .cache import SignatureCache
from .constants import ANALYSIS_MAX_DIM, ANALYSIS_VERSION, FEATURE_MAX_DIM, THUMB_MAX_DIM

DEFAULT_WORKERS = min(12, max(2, (os.cpu_count() or 4) - 1))
MAX_WORKERS = DEFAULT_WORKERS
TINY_SIZE = 24


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(arr.tobytes()).decode("ascii")


def tiny_from_b64(s: Optional[str]) -> Optional[np.ndarray]:
    if not s:
        return None
    try:
        return np.frombuffer(base64.b64decode(s), dtype=np.uint8).reshape(TINY_SIZE, TINY_SIZE)
    except Exception:
        return None


def hist_from_b64(s: Optional[str]) -> Optional[np.ndarray]:
    if not s:
        return None
    try:
        return np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32).reshape(8, 8, 8)
    except Exception:
        return None


def color_signature(bgr: np.ndarray) -> np.ndarray:
    hist = cv2.calcHist([bgr], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    cv2.normalize(hist, hist)
    return hist


def analyze_pixels(bgr_full: np.ndarray) -> Dict[str, Any]:
    """Pure function: compute every analysis signal from a downscaled BGR image."""
    sharp_gray = cv2.cvtColor(bgr_full, cv2.COLOR_BGR2GRAY)
    bgr = image_loader.resize_to_max_dim(bgr_full, FEATURE_MAX_DIM)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    tiny = cv2.resize(gray, (TINY_SIZE, TINY_SIZE), interpolation=cv2.INTER_AREA)
    eq = cv2.equalizeHist(cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA))
    return {
        "blur": blur_detector.compute_blur_signals(sharp_gray),
        "exposure": exposure.compute_exposure_signals(bgr, gray),
        "phash": hashing.phash(gray),
        "dhash": hashing.dhash(gray),
        "ahash": hashing.ahash(gray),
        "ephash": hashing.phash(eq),
        "edhash": hashing.dhash(eq),
        "hist": _b64(color_signature(bgr).astype(np.float16)),
        "tiny": _b64(tiny.astype(np.uint8)),
        "aw": int(bgr.shape[1]),
        "ah": int(bgr.shape[0]),
    }


def analyze_file(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[bytes], Optional[str]]:
    """Decode once, analyse, build a thumbnail. Returns (data, thumb_jpeg, error)."""
    md = metadata.extract_metadata(path)
    bgr, err = image_loader.load_bgr(path, ANALYSIS_MAX_DIM)
    if bgr is None:
        return {"version": ANALYSIS_VERSION, "meta": md.to_json(), "error": err}, None, err
    data = analyze_pixels(bgr)
    data["version"] = ANALYSIS_VERSION
    data["meta"] = md.to_json()
    thumb = image_loader.encode_jpeg(image_loader.resize_to_max_dim(bgr, THUMB_MAX_DIM), 82)
    return data, thumb, None


def cached_or_analyze(path: Path, cache: Optional[SignatureCache]) -> Tuple[Dict[str, Any], bool]:
    """Return (analysis, was_cached). Errors are returned inside the dict as 'error'."""
    if cache is not None:
        hit = cache.get(path)
        if hit and hit.get("version") == ANALYSIS_VERSION and "error" not in hit:
            return hit, True
    data, thumb, _err = analyze_file(path)
    if cache is not None and data is not None and "error" not in data:
        cache.put(path, data, thumb)
    return data or {"error": "analysis failed"}, False


@dataclass
class AnalysisRun:
    total: int = 0
    done: int = 0
    cached: int = 0
    errors: List[Tuple[Path, str]] = field(default_factory=list)
    stopped: bool = False
    elapsed: float = 0.0


def analyze_many(
    paths: List[Path],
    cache: Optional[SignatureCache],
    on_result: Callable[[Path, Dict[str, Any]], None],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
    max_workers: Optional[int] = None,
) -> AnalysisRun:
    """Analyse many files in a bounded thread pool (OpenCV/rawpy release the GIL).

    ``on_result`` is called from the calling thread as results arrive.
    """
    run = AnalysisRun(total=len(paths))
    t0 = time.monotonic()
    if not paths:
        return run
    last = 0.0
    with ThreadPoolExecutor(max_workers=max_workers or MAX_WORKERS) as pool:
        futures = {pool.submit(cached_or_analyze, p, cache): p for p in paths}
        try:
            for fut in as_completed(futures):
                p = futures[fut]
                if should_stop():
                    run.stopped = True
                    for f in futures:
                        f.cancel()
                    break
                try:
                    data, was_cached = fut.result()
                except Exception as e:  # noqa: BLE001
                    data, was_cached = {"error": f"{type(e).__name__}: {e}"}, False
                if "error" in data:
                    run.errors.append((p, str(data["error"])))
                if was_cached:
                    run.cached += 1
                run.done += 1
                on_result(p, data)
                now = time.monotonic()
                if progress_cb and (now - last > 0.1 or run.done == run.total):
                    progress_cb(run.done, run.total, p.name)
                    last = now
        finally:
            if cache is not None:
                cache.flush()
    run.elapsed = time.monotonic() - t0
    return run
