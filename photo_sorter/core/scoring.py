"""Combine sharpness and exposure into one 0-100 quality score per photo."""

from __future__ import annotations

from typing import Optional

from . import blur_detector
from .exposure import QualityResult, score_exposure

FOCUS_PROFILES = {
    "balanced": "Balanced",
    "portrait_subject": "Portrait (subject focus)",
    "landscape_edge": "Landscape (edge to edge)",
    "action_burst": "Action (penalise motion blur)",
}


def compute_quality(
    blur_signals: Optional[dict],
    exposure_signals: Optional[dict],
    sharpness_weight: float = 60.0,
    exposure_weight: float = 40.0,
    focus_profile: str = "balanced",
) -> QualityResult:
    total = max(0.001, sharpness_weight + exposure_weight)
    sw, ew = sharpness_weight / total, exposure_weight / total
    reasons, tags = [], []

    if blur_signals:
        sharp = blur_detector.profile_score(blur_signals, focus_profile)
        if sharp >= 80:
            s_label = "Tack sharp"
        elif sharp >= 62:
            s_label = "Sharp"
        elif sharp >= 45:
            s_label = "Slightly soft"
        else:
            s_label = "Blurry"
        if blur_signals.get("edge_regions", 0) < 2:
            s_label = "No detail"
            sharp = min(sharp, 10.0)
        if blur_signals.get("motion_ratio", 0) > 0.28 and sharp < 62:
            tags.append("motion_blur")
            reasons.append("Motion blur / camera shake")
        if blur_signals.get("top_score", 0) - blur_signals.get("median_score", 0) > 25 and sharp >= 62:
            tags.append("selective_focus")
            reasons.append("Sharp subject, soft background")
        if focus_profile == "landscape_edge" and blur_signals.get("low_score", 100) < 45 <= sharp:
            tags.append("corner_softness")
            reasons.append("Soft areas towards the frame edges")
    else:
        sharp, s_label = 50.0, "Unknown"

    if exposure_signals:
        er = score_exposure(exposure_signals)
        exp_score, e_label = er.score, er.label
        tags.extend(er.tags)
        reasons.extend(er.reasons[:2])
    else:
        exp_score, e_label = 50.0, "Unknown"

    quality = max(0.0, min(100.0, sw * sharp + ew * exp_score))
    # A badly blurred frame is never a "good" photo however nice the exposure is.
    if sharp < 30:
        quality = min(quality, 35.0 + sharp / 2)
    if sharp >= 75 and exp_score >= 75:
        tags.append("top_quality")
        reasons.insert(0, "Sharp and well exposed")
    elif sharp < 45:
        reasons.insert(0, f"Soft / blurry (sharpness {sharp:.0f})")

    return QualityResult(
        quality_score=round(quality, 1),
        sharpness_score=round(sharp, 1),
        exposure_score=round(exp_score, 1),
        sharpness_raw_composite=round(float((blur_signals or {}).get("composite_score", 0.0)), 1),
        exposure_label=e_label,
        sharpness_label=s_label,
        reasons=reasons,
        tags=tags,
        subject_sharpness=round(float((blur_signals or {}).get("top_score", 0.0)), 1),
        bokeh_ratio=1.0,
        focus_profile=focus_profile,
    )
