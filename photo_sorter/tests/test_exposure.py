"""Tests for photo_sorter.core.exposure module."""

import cv2
import numpy as np
from photo_sorter.core import blur_detector as bd
from photo_sorter.core import exposure as exp
from .conftest import make_sharp_image, make_dark_image, make_bright_image


def make_well_exposed_image(size: int = 400) -> np.ndarray:
    """Natural gradient and textured scene with rich dynamic range, mean ~128."""
    x = np.linspace(30, 225, size, dtype=np.uint8)
    img = np.tile(x, (size, 1))
    noise = np.random.default_rng(42).integers(-10, 10, (size, size), endpoint=True).astype(np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def make_blown_highlights_image(size: int = 400) -> np.ndarray:
    """Image where 30% of the pixels are clipped at 255."""
    img = make_well_exposed_image(size)
    img[: int(size * 0.3), :] = 255
    return img


def make_crushed_shadows_image(size: int = 400) -> np.ndarray:
    """Image where 30% of the pixels are clipped at 0."""
    img = make_well_exposed_image(size)
    img[: int(size * 0.3), :] = 0
    return img


def test_exposure_signals_computation():
    img = make_well_exposed_image()
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    signals = exp.compute_exposure_signals(bgr, img)

    assert "mean_brightness" in signals
    assert "std_contrast" in signals
    assert "highlight_clip_frac" in signals
    assert "shadow_clip_frac" in signals
    assert "dynamic_range_utilization" in signals
    assert "tonal_zones" in signals
    assert "histogram_256" in signals
    assert len(signals["histogram_256"]) == 64
    assert 110 <= signals["mean_brightness"] <= 145


def test_well_exposed_scores_higher_than_blown_highlights():
    well = make_well_exposed_image()
    blown = make_blown_highlights_image()

    bgr_well = cv2.cvtColor(well, cv2.COLOR_GRAY2BGR)
    bgr_blown = cv2.cvtColor(blown, cv2.COLOR_GRAY2BGR)

    sig_well = exp.compute_exposure_signals(bgr_well, well)
    sig_blown = exp.compute_exposure_signals(bgr_blown, blown)

    res_well = exp.score_exposure(sig_well)
    res_blown = exp.score_exposure(sig_blown)

    assert res_well.score > res_blown.score
    assert "blown_highlights" in res_blown.tags
    assert res_well.label in ("Excellent", "Good")


def test_well_exposed_scores_higher_than_crushed_shadows():
    well = make_well_exposed_image()
    crushed = make_crushed_shadows_image()

    bgr_well = cv2.cvtColor(well, cv2.COLOR_GRAY2BGR)
    bgr_crushed = cv2.cvtColor(crushed, cv2.COLOR_GRAY2BGR)

    sig_well = exp.compute_exposure_signals(bgr_well, well)
    sig_crushed = exp.compute_exposure_signals(bgr_crushed, crushed)

    res_well = exp.score_exposure(sig_well)
    res_crushed = exp.score_exposure(sig_crushed)

    assert res_well.score > res_crushed.score
    assert "crushed_shadows" in res_crushed.tags


def test_severe_underexposure_tagged_underexposed():
    sharp = make_sharp_image()
    dark = make_dark_image(sharp, factor=0.08)
    bgr_dark = cv2.cvtColor(dark, cv2.COLOR_GRAY2BGR)

    sig_dark = exp.compute_exposure_signals(bgr_dark, dark)
    res_dark = exp.score_exposure(sig_dark)

    assert "underexposed" in res_dark.tags
    assert res_dark.score < 60.0


def test_severe_overexposure_tagged_overexposed():
    sharp = make_sharp_image()
    bright = make_bright_image(sharp, add=210)
    bgr_bright = cv2.cvtColor(bright, cv2.COLOR_GRAY2BGR)

    sig_bright = exp.compute_exposure_signals(bgr_bright, bright)
    res_bright = exp.score_exposure(sig_bright)

    assert "overexposed" in res_bright.tags
    assert res_bright.score < 60.0


def test_combined_quality_scoring():
    from photo_sorter.core import scoring

    sharp = make_sharp_image()
    well = make_well_exposed_image()
    bgr_well = cv2.cvtColor(well, cv2.COLOR_GRAY2BGR)
    quality = scoring.compute_quality(bd.compute_blur_signals(sharp), exp.compute_exposure_signals(bgr_well, well),
                                      sharpness_weight=70, exposure_weight=30)
    assert quality.quality_score >= 70.0
    assert quality.sharpness_score >= 60.0
    assert quality.exposure_score >= 70.0
    assert "top_quality" in quality.tags


def test_blurry_photo_quality_is_capped_even_if_well_exposed():
    from photo_sorter.core import scoring
    from .conftest import make_blurry_image

    well = make_well_exposed_image()
    bgr_well = cv2.cvtColor(well, cv2.COLOR_GRAY2BGR)
    blurry = make_blurry_image(make_sharp_image(), ksize=31)
    q = scoring.compute_quality(bd.compute_blur_signals(blurry), exp.compute_exposure_signals(bgr_well, well),
                                sharpness_weight=30, exposure_weight=70)
    assert q.sharpness_score < 45
    assert q.quality_score < 50


def test_json_roundtrip():
    well = make_well_exposed_image()
    bgr = cv2.cvtColor(well, cv2.COLOR_GRAY2BGR)
    signals = exp.compute_exposure_signals(bgr, well)
    res = exp.score_exposure(signals)
    d = res.to_json()
    res2 = exp.ExposureResult.from_json(d)
    assert res.score == res2.score
    assert res.label == res2.label
