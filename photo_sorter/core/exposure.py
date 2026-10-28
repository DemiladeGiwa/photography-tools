"""
Exposure evaluation and quality scoring engine for RAW and raster photos.

Analyzes luminance distribution, highlight clipping (blown highlights), shadow
crushing (lost shadow details), midtone exposure balance, and dynamic range to
provide photographers with an objective 0-100 Exposure Score and combined
Quality ranking metrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# Ideal photographer midtone luminance target zone (0-255 scale)
TARGET_LUMINANCE_LOW = 105.0
TARGET_LUMINANCE_HIGH = 145.0
TARGET_LUMINANCE_CENTER = 125.0


@dataclass
class ExposureResult:
    score: float  # 0.0 to 100.0
    label: str  # "Excellent", "Good", "Fair", "Under-exposed", "Over-exposed"
    mean_brightness: float
    std_contrast: float
    highlight_clip_frac: float
    shadow_clip_frac: float
    dynamic_range_utilization: float
    tonal_zones: Dict[str, float]  # blacks, shadows, midtones, highlights, whites
    reasons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    histogram_256: List[float] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "ExposureResult":
        return ExposureResult(**d)


@dataclass
class QualityResult:
    quality_score: float  # 0.0 to 100.0
    sharpness_score: float  # 0.0 to 100.0
    exposure_score: float  # 0.0 to 100.0
    sharpness_raw_composite: float
    exposure_label: str
    sharpness_label: str
    reasons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    subject_sharpness: float = 0.0
    bokeh_ratio: float = 1.0
    focus_profile: str = "balanced"

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "QualityResult":
        valid_keys = {
            "quality_score", "sharpness_score", "exposure_score",
            "sharpness_raw_composite", "exposure_label", "sharpness_label",
            "reasons", "tags", "subject_sharpness", "bokeh_ratio", "focus_profile"
        }
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return QualityResult(**filtered)


def _bins64(hist: np.ndarray) -> List[float]:
    """Collapse a 256-bin histogram to 64 bins (plenty for display)."""
    h = np.asarray(hist, dtype=np.float64).reshape(64, 4).sum(axis=1)
    return [round(float(v), 5) for v in h]


def compute_exposure_signals(bgr: np.ndarray, gray: Optional[np.ndarray] = None) -> dict:
    """Compute raw pixel exposure and luminance signals from an image buffer.
    
    Pure function of pixels — cached in SQLite for fast multi-pass filtering.
    """
    if gray is None:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if len(bgr.shape) == 3 else bgr

    h, w = gray.shape[:2]
    total_pixels = max(1, h * w)

    # 1. 256-bin luminance histogram
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    hist_flat = hist.flatten()
    hist_norm = hist_flat / float(total_pixels)

    mean_brightness = float(gray.mean())
    std_contrast = float(gray.std())

    # 2. RGB histograms and channel-specific clipping
    hist_rgb: Dict[str, List[float]] = {}
    r_clip = 0.0
    g_clip = 0.0
    b_clip = 0.0
    specular_clip_frac = 0.0
    area_clip_frac = 0.0

    if len(bgr.shape) == 3:
        b_ch = bgr[:, :, 0]
        g_ch = bgr[:, :, 1]
        r_ch = bgr[:, :, 2]

        hist_b = cv2.calcHist([b_ch], [0], None, [256], [0, 256]).flatten() / float(total_pixels)
        hist_g = cv2.calcHist([g_ch], [0], None, [256], [0, 256]).flatten() / float(total_pixels)
        hist_r = cv2.calcHist([r_ch], [0], None, [256], [0, 256]).flatten() / float(total_pixels)

        hist_rgb = {"r": _bins64(hist_r), "g": _bins64(hist_g), "b": _bins64(hist_b)}

        r_clip = float(np.sum(r_ch >= 252)) / float(total_pixels)
        g_clip = float(np.sum(g_ch >= 252)) / float(total_pixels)
        b_clip = float(np.sum(b_ch >= 252)) / float(total_pixels)

        max_channel = np.maximum(np.maximum(r_ch, g_ch), b_ch)
        min_channel = np.minimum(np.minimum(r_ch, g_ch), b_ch)
        highlight_clip_frac = float(np.sum(max_channel >= 252)) / float(total_pixels)
        shadow_clip_frac = float(np.sum(min_channel <= 6)) / float(total_pixels)

        # Distinguish tiny specular highlights (catchlights, sun glints) from broad area blowout
        blown_mask = (max_channel >= 252).astype(np.uint8)
        if highlight_clip_frac > 0.001:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(blown_mask, connectivity=8)
            # Component sizes (excluding background label 0)
            sizes = stats[1:, cv2.CC_STAT_AREA] if num_labels > 1 else np.array([])
            large_area_pixels = np.sum(sizes[sizes >= 80]) if len(sizes) > 0 else 0
            area_clip_frac = float(large_area_pixels) / float(total_pixels)
            specular_clip_frac = max(0.0, highlight_clip_frac - area_clip_frac)
        else:
            specular_clip_frac = highlight_clip_frac
    else:
        norm_mono = _bins64(hist_norm)
        hist_rgb = {"r": norm_mono, "g": norm_mono, "b": norm_mono}
        highlight_clip_frac = float(np.sum(gray >= 252)) / float(total_pixels)
        shadow_clip_frac = float(np.sum(gray <= 6)) / float(total_pixels)
        r_clip = highlight_clip_frac
        g_clip = highlight_clip_frac
        b_clip = highlight_clip_frac
        area_clip_frac = highlight_clip_frac

    # 3. Tonal zones: Blacks (0-25), Shadows (26-85), Midtones (86-170), Highlights (171-230), Whites (231-255)
    blacks = float(np.sum(hist_norm[0:26]))
    shadows = float(np.sum(hist_norm[26:86]))
    midtones = float(np.sum(hist_norm[86:171]))
    highlights = float(np.sum(hist_norm[171:231]))
    whites = float(np.sum(hist_norm[231:256]))

    tonal_zones = {
        "blacks": round(blacks, 4),
        "shadows": round(shadows, 4),
        "midtones": round(midtones, 4),
        "highlights": round(highlights, 4),
        "whites": round(whites, 4),
    }

    # 4. Dynamic range utilization (percentile 1% to 99%)
    p1 = float(np.percentile(gray, 1))
    p99 = float(np.percentile(gray, 99))
    dynamic_range_utilization = max(0.0, min(100.0, (p99 - p1) / 255.0 * 100.0))

    return {
        "mean_brightness": round(mean_brightness, 2),
        "std_contrast": round(std_contrast, 2),
        "highlight_clip_frac": round(highlight_clip_frac, 5),
        "shadow_clip_frac": round(shadow_clip_frac, 5),
        "r_clip_frac": round(r_clip, 5),
        "g_clip_frac": round(g_clip, 5),
        "b_clip_frac": round(b_clip, 5),
        "specular_clip_frac": round(specular_clip_frac, 5),
        "area_clip_frac": round(area_clip_frac, 5),
        "dynamic_range_utilization": round(dynamic_range_utilization, 2),
        "tonal_zones": tonal_zones,
        "histogram_256": _bins64(hist_norm),  # stored at 64 bins to keep the cache small
        "histogram_rgb": hist_rgb,
    }


def score_exposure(signals: dict) -> ExposureResult:
    """Evaluate exposure signals and return an objective 0-100 score and diagnosis."""
    mean_b = signals.get("mean_brightness", 128.0)
    std_c = signals.get("std_contrast", 40.0)
    hl_clip = signals.get("highlight_clip_frac", 0.0)
    sh_clip = signals.get("shadow_clip_frac", 0.0)
    r_clip = signals.get("r_clip_frac", 0.0)
    b_clip = signals.get("b_clip_frac", 0.0)
    spec_clip = signals.get("specular_clip_frac", 0.0)
    area_clip = signals.get("area_clip_frac", hl_clip)
    dr_util = signals.get("dynamic_range_utilization", 70.0)
    tonal = signals.get("tonal_zones", {})
    hist_256 = signals.get("histogram_256", [])

    base_score = 100.0
    reasons = []
    tags = []

    # 1. Highlight blowout penalty
    # Distinguish broad area blowout (destructive) vs specular catchlights
    if area_clip > 0.006:
        excess_hl = area_clip - 0.006
        penalty_hl = min(42.0, excess_hl * 450.0)
        base_score -= penalty_hl
        reasons.append(f"Blown highlight areas ({area_clip * 100:.1f}% clipped) [-{penalty_hl:.0f} pts]")
        tags.append("blown_highlights")
    elif hl_clip > 0.015:
        penalty_hl = min(15.0, (hl_clip - 0.015) * 200.0)
        base_score -= penalty_hl
        reasons.append(f"Specular highlight reflections ({hl_clip * 100:.1f}%) [-{penalty_hl:.0f} pts]")
        tags.append("specular_glints")
    elif hl_clip < 0.001 and mean_b > 180:
        tags.append("bright_key")

    # Single-channel clipping check (e.g. red blowout in sunset/neon or blue blowout)
    if (r_clip > 0.03 and hl_clip < 0.01) or (b_clip > 0.03 and hl_clip < 0.01):
        penalty_ch = 8.0
        base_score -= penalty_ch
        reasons.append(f"Color channel clipping detected [-{penalty_ch:.0f} pts]")
        tags.append("color_channel_clipping")

    # 2. Shadow crushing penalty
    if sh_clip > 0.025:
        excess_sh = sh_clip - 0.025
        penalty_sh = min(25.0, excess_sh * 200.0)
        base_score -= penalty_sh
        reasons.append(f"Crushed dark shadows ({sh_clip * 100:.1f}% black) [-{penalty_sh:.0f} pts]")
        tags.append("crushed_shadows")

    # 3. Overall luminance deviation & creative exposure recognition
    if mean_b < TARGET_LUMINANCE_LOW:
        dev = TARGET_LUMINANCE_LOW - mean_b
        # Low light / underexposure penalty
        if sh_clip < 0.015 and std_c >= 35.0 and mean_b >= 75.0:
            # Intentional moody / low-key exposure
            penalty_mean = min(15.0, (dev / 75.0) ** 1.3 * 16.0)
            base_score -= penalty_mean
            tags.append("low_key_moody")
            reasons.append(f"Moody low-key exposure balance (mean {mean_b:.0f}/255)")
        else:
            penalty_mean = min(35.0, (dev / 75.0) ** 1.4 * 35.0)
            base_score -= penalty_mean
            if mean_b < 60:
                reasons.append(f"Severe underexposure (mean luminance {mean_b:.0f}/255) [-{penalty_mean:.0f} pts]")
                tags.append("underexposed")
            else:
                reasons.append(f"Slightly dark exposure (mean luminance {mean_b:.0f}/255) [-{penalty_mean:.0f} pts]")
    elif mean_b > TARGET_LUMINANCE_HIGH:
        dev = mean_b - TARGET_LUMINANCE_HIGH
        if area_clip < 0.003 and std_c >= 32.0:
            # ETTR (Expose To The Right) or High-Key with preserved highlight details
            penalty_mean = min(14.0, (dev / 75.0) ** 1.3 * 15.0)
            base_score -= penalty_mean
            tags.append("ettr_balanced")
            reasons.append(f"Bright ETTR exposure (mean {mean_b:.0f}/255, clean highlights)")
        else:
            penalty_mean = min(35.0, (dev / 75.0) ** 1.4 * 35.0)
            base_score -= penalty_mean
            if mean_b > 190:
                reasons.append(f"Severe overexposure (mean luminance {mean_b:.0f}/255) [-{penalty_mean:.0f} pts]")
                tags.append("overexposed")
            else:
                reasons.append(f"Slightly bright exposure (mean luminance {mean_b:.0f}/255) [-{penalty_mean:.0f} pts]")

    # 4. Contrast and dynamic range modulation
    if std_c < 24.0:
        penalty_c = min(20.0, (24.0 - std_c) * 1.5)
        base_score -= penalty_c
        reasons.append(f"Low contrast / washed out (std={std_c:.1f}) [-{penalty_c:.0f} pts]")
        tags.append("low_contrast")
    elif std_c >= 45.0 and dr_util > 75.0:
        tags.append("high_dynamic_range")

    final_score = max(5.0, min(100.0, round(base_score, 1)))

    # Classification label
    if final_score >= 88.0:
        label = "Excellent"
        tags.append("balanced_exposure")
        if not reasons:
            reasons.append("Clean exposure balance with rich dynamic range")
    elif final_score >= 72.0:
        label = "Good"
        tags.append("balanced_exposure")
        if not reasons:
            reasons.append("Well-balanced exposure within acceptable tolerances")
    elif final_score >= 50.0:
        label = "Fair"
    elif "underexposed" in tags:
        label = "Under-exposed"
    elif "overexposed" in tags:
        label = "Over-exposed"
    else:
        label = "Sub-optimal"

    return ExposureResult(
        score=final_score,
        label=label,
        mean_brightness=mean_b,
        std_contrast=std_c,
        highlight_clip_frac=hl_clip,
        shadow_clip_frac=sh_clip,
        dynamic_range_utilization=dr_util,
        tonal_zones=tonal,
        reasons=reasons,
        tags=tags,
        histogram_256=hist_256,
    )
