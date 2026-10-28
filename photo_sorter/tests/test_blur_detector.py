"""Sharpness detection: must separate sharp from blurred regardless of subject matter."""

import cv2
import numpy as np

from photo_sorter.core import blur_detector as bd

from .conftest import (make_blurry_image, make_dark_image, make_low_texture_image, make_motion_blurred_image,
                       make_noisy_image, make_scene, make_sharp_image, make_sharp_subject_blurred_bg)


def _gray(seed):
    return cv2.cvtColor(make_scene(seed), cv2.COLOR_BGR2GRAY)


def test_sharp_scores_high_and_blur_scores_low():
    sharp = _gray(3)
    s_sharp = bd.compute_blur_signals(sharp)["composite_score"]
    s_soft = bd.compute_blur_signals(cv2.GaussianBlur(sharp, (0, 0), 1.5))["composite_score"]
    s_blur = bd.compute_blur_signals(cv2.GaussianBlur(sharp, (0, 0), 3))["composite_score"]
    assert s_sharp > 80
    assert s_sharp > s_soft > s_blur
    assert s_blur < 35


def test_score_is_content_independent():
    """Sharp photos of very different subjects should land in the same band (old metric varied 100x)."""
    scores = [bd.compute_blur_signals(g)["composite_score"] for g in (_gray(1), _gray(2), make_sharp_image(600))]
    assert min(scores) > 70
    assert max(scores) - min(scores) < 30


def test_classify_labels():
    sharp = _gray(5)
    assert bd.classify(bd.compute_blur_signals(sharp), 55).label == "Sharp"
    res = bd.classify(bd.compute_blur_signals(make_blurry_image(sharp, 31)), 55)
    assert res.label == "Blurry" and res.is_blurry
    assert "defocus" in res.tags


def test_motion_blur_detected_as_directional():
    sharp = _gray(6)
    sig = bd.compute_blur_signals(make_motion_blurred_image(sharp, 21))
    res = bd.classify(sig, 55)
    assert res.is_blurry
    assert "motion_blur" in res.tags
    assert sig["motion_ratio"] > 0.28


def test_noise_and_darkness_do_not_fake_sharpness():
    soft = cv2.GaussianBlur(_gray(7), (0, 0), 3)
    base = bd.compute_blur_signals(soft)["composite_score"]
    assert bd.compute_blur_signals(make_noisy_image(soft, 10))["composite_score"] < base + 20
    assert bd.compute_blur_signals(make_dark_image(soft, 0.3))["composite_score"] < 45


def test_featureless_frame_is_flagged():
    res = bd.classify(bd.compute_blur_signals(make_low_texture_image()), 55)
    assert res.label == "Blurry"
    assert "no_detail" in res.tags


def test_portrait_profile_forgives_soft_background():
    img = make_sharp_subject_blurred_bg(600)
    sig = bd.compute_blur_signals(img)
    assert bd.profile_score(sig, "portrait_subject") >= bd.profile_score(sig, "landscape_edge")


def test_heatmap_overlay_shapes():
    sharp = make_scene(9)
    sig = bd.compute_blur_signals(cv2.cvtColor(sharp, cv2.COLOR_BGR2GRAY))
    res = bd.classify(sig, 55)
    assert len(res.heatmap) == 5 and len(res.heatmap[0]) == 5
    out = bd.generate_heatmap_overlay(sharp, res.heatmap, threshold=55)
    assert out.shape == sharp.shape
