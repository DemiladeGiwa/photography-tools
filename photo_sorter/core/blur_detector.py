"""
Sharpness / blur detection based on *edge steepness*.

Why this approach
-----------------
The previous version scored "Laplacian variance". That number mostly
measures how much texture a photo has: a sharp foggy landscape scored 20
while a sharp photo of a newspaper scored 1300, so no single threshold could
work. Here we measure how *steep* edges are relative to their own contrast:

    steepness = |gradient| / local contrast     (at edge ridge pixels)

A crisp edge rises over ~1 pixel (steepness ~0.8); the same edge blurred by
1.5 px rises over several pixels (~0.55). Because it is normalised by local
contrast, the value hardly changes with subject matter, brightness or ISO
noise - only with focus and motion.

Signals computed (all cached):
* a 5x5 map of regional sharpness (for the heat-map and subject focus),
* sharpness per edge orientation (motion blur blurs one direction only),
* top-region, median and low-region sharpness, mapped to a 0-100 scale,
* noise and brightness estimates (reported, used for confidence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np

GRID = 5
SIGNAL_VERSION = 2

# Sensitivity presets on the 0-100 sharpness scale.
PRESET_THRESHOLDS = {
    "Conservative": 42,  # only flags clearly soft frames
    "Balanced": 55,      # recommended
    "Aggressive": 68,    # flags anything that isn't crisp
}

_E_LOW, _E_HIGH = 0.30, 0.80  # steepness mapped to 0 and 100


@dataclass
class BlurResult:
    composite_score: float
    threshold: float
    label: str  # Blurry | Probably Blurry | Probably Sharp | Sharp
    confidence: float
    reasons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    noise_sigma: float = 0.0
    mean_brightness: float = 0.0
    is_blurry: bool = False
    heatmap: List[List[float]] = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)


def to_score(steepness: float) -> float:
    return float(np.clip((steepness - _E_LOW) / (_E_HIGH - _E_LOW), 0.0, 1.0) * 100.0)


def _estimate_noise_sigma(gray_f: np.ndarray) -> float:
    """Immerkaer (1996) fast noise estimate."""
    if min(gray_f.shape) < 3:
        return 0.0
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    conv = cv2.filter2D(gray_f, -1, k)
    return float(np.sqrt(np.pi / 2) * np.mean(np.abs(conv)) / 6.0)


def compute_blur_signals(gray: np.ndarray) -> Dict:
    """Pure function of pixels. Expects a grey image ~1000-1600 px on the long edge."""
    I0 = gray.astype(np.float32)
    h, w = I0.shape[:2]
    noise = _estimate_noise_sigma(I0)
    I = cv2.GaussianBlur(I0, (0, 0), 0.7)
    gx = cv2.Sobel(I, cv2.CV_32F, 1, 0, ksize=3) / 4.0
    gy = cv2.Sobel(I, cv2.CV_32F, 0, 1, ksize=3) / 4.0
    G = cv2.magnitude(gx, gy)
    k7 = np.ones((7, 7), np.uint8)
    C = cv2.dilate(I, k7) - cv2.erode(I, k7)
    contrast_floor = min(60.0, max(18.0, 6.0 * noise))
    ridge = (G >= cv2.dilate(G, np.ones((3, 3), np.uint8)) - 1e-4) & (C > contrast_floor)
    R = G / np.maximum(C, 1.0)

    mean_brightness = float(I0.mean())
    dark_frac = float(np.mean(I0 < 12))
    bright_frac = float(np.mean(I0 > 244))

    ys, xs = np.nonzero(ridge)
    vals_all = R[ys, xs]
    edge_px = int(vals_all.size)
    global_e = float(np.percentile(vals_all, 90)) if edge_px >= 50 else 0.0

    # Orientation bins (gradient direction): motion blur softens one direction only.
    # Work on the edge pixels only - far cheaper than whole-image arrays.
    ang = (np.degrees(np.arctan2(gy[ys, xs], gx[ys, xs])) + 180.0) % 180.0
    orient: List[Optional[float]] = []
    for a0 in (0.0, 45.0, 90.0, 135.0):
        d = np.abs(((ang - a0) + 90.0) % 180.0 - 90.0)
        v = vals_all[d < 22.5]
        orient.append(float(np.percentile(v, 90)) if v.size >= 40 else None)
    present = sorted(x for x in orient if x is not None)
    if len(present) >= 3 and global_e > 0:
        low3 = float(np.mean(present[:3]))
        direction_factor = min(1.0, low3 / max(present[-1], 1e-6))
        motion_ratio = 1.0 - present[0] / max(present[-1], 1e-6)
        motion_angle = (0, 45, 90, 135)[orient.index(present[0])]
    else:
        direction_factor, motion_ratio, motion_angle = 1.0, 0.0, None
    # Natural sharp photos sit around 0.85-0.95; motion-blurred ones drop to 0.5-0.7.
    motion_keep = float(np.clip((direction_factor - 0.55) / (0.90 - 0.55), 0.0, 1.0))

    # Regional map.
    ph, pw = max(1, h // GRID), max(1, w // GRID)
    min_px = max(20, int(ph * pw * 0.0015))
    region_scores: List[List[Optional[float]]] = [[None] * GRID for _ in range(GRID)]
    region_vals = []
    cell = np.minimum(ys // ph, GRID - 1) * GRID + np.minimum(xs // pw, GRID - 1)
    order = np.argsort(cell, kind="stable")
    cell_sorted, vals_sorted = cell[order], vals_all[order]
    bounds = np.searchsorted(cell_sorted, np.arange(GRID * GRID + 1))
    for idx in range(GRID * GRID):
        v = vals_sorted[bounds[idx]:bounds[idx + 1]]
        if v.size >= min_px:
            r, c = divmod(idx, GRID)
            e = float(np.percentile(v, 90))
            # Motion blur affects the whole frame: scale regions by how one-sided the softness is.
            e_adj = _E_LOW + (e - _E_LOW) * (0.25 + 0.75 * motion_keep)
            sc = round(to_score(e_adj), 1)
            region_scores[r][c] = sc
            region_vals.append((sc, r, c))

    scores = np.array([s for s, _, _ in region_vals], dtype=np.float32)
    if scores.size:
        ordered = np.sort(scores)[::-1]
        top = float(np.mean(ordered[: min(3, ordered.size)]))
        median = float(np.median(scores))
        low = float(np.percentile(scores, 25))
        center = [s for s, r, c in region_vals if 1 <= r <= 3 and 1 <= c <= 3]
        center_top = float(np.max(center)) if center else top
        outer = [s for s, r, c in region_vals if not (1 <= r <= 3 and 1 <= c <= 3)]
        outer_med = float(np.median(outer)) if outer else median
    else:
        top = median = low = center_top = outer_med = 0.0

    composite = 0.6 * top + 0.4 * median
    grid_matrix = [[(v if v is not None else -1.0) for v in row] for row in region_scores]
    return {
        "signal_version": SIGNAL_VERSION,
        "composite_score": round(composite, 2),
        "top_score": round(top, 2),
        "median_score": round(median, 2),
        "low_score": round(low, 2),
        "center_top": round(center_top, 2),
        "outer_median": round(outer_med, 2),
        "global_steepness": round(global_e, 4),
        "orientation": [None if x is None else round(x, 4) for x in orient],
        "direction_factor": round(direction_factor, 3),
        "motion_ratio": round(motion_ratio, 3),
        "motion_angle": motion_angle,
        "mean_anisotropy": round(motion_ratio, 3),
        "edge_regions": int(scores.size),
        "edge_pixels": edge_px,
        "noise_sigma": round(noise, 2),
        "mean_brightness": round(mean_brightness, 2),
        "dark_frac": round(dark_frac, 4),
        "bright_frac": round(bright_frac, 4),
        "grid_matrix": grid_matrix,
    }


def profile_score(signals: Dict, profile: str = "balanced") -> float:
    """Sharpness 0-100 as judged by a focus profile."""
    if not signals:
        return 0.0
    top = signals.get("top_score", 0.0)
    med = signals.get("median_score", 0.0)
    low = signals.get("low_score", 0.0)
    if profile == "portrait_subject":
        return float(max(signals.get("composite_score", 0.0), 0.85 * top + 0.15 * med))
    if profile == "landscape_edge":
        return float(0.35 * top + 0.35 * med + 0.30 * low)
    if profile == "action_burst":
        penalty = max(0.0, signals.get("motion_ratio", 0.0) - 0.22) * 60.0
        return float(max(0.0, signals.get("composite_score", 0.0) - penalty))
    return float(signals.get("composite_score", 0.0))


def classify(signals: Dict, threshold: float, profile: str = "balanced") -> BlurResult:
    """Label a photo against a threshold. Cheap - safe to call while a slider moves."""
    score = profile_score(signals, profile)
    noise = signals.get("noise_sigma", 0.0)
    brightness = signals.get("mean_brightness", 128.0)
    regions = signals.get("edge_regions", 0)
    motion = signals.get("motion_ratio", 0.0)

    band = 7.0
    if regions < 2:
        label = "Blurry"
    elif score < threshold - band:
        label = "Blurry"
    elif score < threshold:
        label = "Probably Blurry"
    elif score < threshold + band:
        label = "Probably Sharp"
    else:
        label = "Sharp"

    confidence = 55.0 + 40.0 * min(1.0, abs(score - threshold) / (band * 2.5))
    reasons, tags = [], []
    blurry = label in ("Blurry", "Probably Blurry")
    if regions < 2:
        reasons.append("Almost no detail or edges found (lens cap, fog, or completely out of focus)")
        tags.append("no_detail")
        confidence = 60.0
    elif blurry:
        reasons.append(f"Sharpness {score:.0f} is below your threshold of {threshold:.0f}")
        if motion > 0.28:
            reasons.append("Edges are soft in one direction only - looks like motion blur / camera shake")
            tags.append("motion_blur")
        else:
            reasons.append("Edges are soft in every direction - looks like missed focus")
            tags.append("defocus")
    else:
        reasons.append(f"Sharpness {score:.0f} meets your threshold of {threshold:.0f}")
        top, med = signals.get("top_score", 0), signals.get("median_score", 0)
        if top - med > 25:
            reasons.append("Sharp subject with a soft background (shallow depth of field)")
            tags.append("selective_focus")
    if noise > 8:
        tags.append("noisy")
        reasons.append(f"High noise (sigma {noise:.0f}) - verdict less certain")
        confidence *= 0.9
    if brightness < 30:
        tags.append("dark")
        reasons.append("Very dark frame - verdict less certain")
        confidence *= 0.85
    elif brightness > 230:
        tags.append("bright")
        confidence *= 0.9

    ref = max(1.0, threshold)
    heatmap = [[(-1.0 if v < 0 else round(v / ref, 2)) for v in row] for row in signals.get("grid_matrix", [])]
    return BlurResult(
        composite_score=round(score, 1), threshold=threshold, label=label,
        confidence=round(max(50.0, min(99.0, confidence)), 1), reasons=reasons, tags=tags,
        noise_sigma=noise, mean_brightness=brightness, is_blurry=blurry, heatmap=heatmap,
    )


def generate_heatmap_overlay(bgr: np.ndarray, heatmap: List[List[float]], alpha: float = 0.5,
                             threshold: Optional[float] = None) -> np.ndarray:
    """Tint each region red (soft) -> yellow -> green (sharp). Regions with no detail stay untinted."""
    if not heatmap:
        return bgr
    h, w = bgr.shape[:2]
    rows, cols = len(heatmap), len(heatmap[0])
    overlay = bgr.copy()
    mask = np.zeros((h, w), np.uint8)
    for r in range(rows):
        for c in range(cols):
            v = heatmap[r][c]
            y1, y2 = int(r * h / rows), int((r + 1) * h / rows)
            x1, x2 = int(c * w / cols), int((c + 1) * w / cols)
            if v < 0:
                continue
            t = float(np.clip(v, 0.0, 1.3)) / 1.3
            if t < 0.6:  # red -> amber
                u = t / 0.6
                color = (30, int(30 + 150 * u), 230)
            else:  # amber -> green
                u = (t - 0.6) / 0.4
                color = (40, int(180 + 30 * u), int(230 * (1 - u)))
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            mask[y1:y2, x1:x2] = 1
    blended = cv2.addWeighted(overlay, alpha, bgr, 1 - alpha, 0)
    out = np.where(mask[..., None] == 1, blended, bgr)
    for r in range(1, rows):
        cv2.line(out, (0, int(r * h / rows)), (w, int(r * h / rows)), (30, 30, 30), 1)
    for c in range(1, cols):
        cv2.line(out, (int(c * w / cols), 0), (int(c * w / cols), h), (30, 30, 30), 1)
    if threshold:  # print each region's sharpness score
        fs = max(0.5, min(w, h) / 900.0)
        th = max(1, int(round(fs * 2)))
        for r in range(rows):
            for c in range(cols):
                v = heatmap[r][c]
                txt = "-" if v < 0 else f"{v * threshold:.0f}"
                (tw, tht), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
                x = int((c + 0.5) * w / cols - tw / 2)
                y = int((r + 0.5) * h / rows + tht / 2)
                cv2.putText(out, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, fs, (20, 20, 20), th + 3, cv2.LINE_AA)
                cv2.putText(out, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
    return out


def threshold_for_sensitivity(sensitivity: str, advanced_value: Optional[float] = None) -> float:
    if advanced_value is not None:
        return float(advanced_value)
    return float(PRESET_THRESHOLDS.get(sensitivity, PRESET_THRESHOLDS["Balanced"]))
