from datetime import datetime, timedelta

import cv2
import numpy as np

from photo_sorter.core import analysis, grouping

from .conftest import make_scene


def _item(key, bgr, t=None, sharp=50.0):
    d = analysis.analyze_pixels(bgr)
    return grouping.GroupInput(key=key, name=key, time=t, phash=d["phash"], dhash=d["dhash"], ephash=d["ephash"],
                               edhash=d["edhash"], tiny=analysis.tiny_from_b64(d["tiny"]),
                               hist=analysis.hist_from_b64(d["hist"]), sharpness=sharp, exposure=80, quality=70)


def _shift(img, dx, dy):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


def test_bursts_group_and_scenes_stay_apart():
    t = datetime(2026, 1, 1, 12)
    a, b = make_scene(1), make_scene(2)
    items = [_item("a1", a, t), _item("a2", _shift(a, 4, 2), t + timedelta(seconds=1)),
             _item("a3", _shift(a, -3, 1), t + timedelta(seconds=2)),
             _item("b1", b, t + timedelta(seconds=40)), _item("b2", _shift(b, 2, 2), t + timedelta(seconds=41))]
    groups = grouping.compute_groups(items)
    sets = sorted(sorted(g.members) for g in groups)
    assert sets == [["a1", "a2", "a3"], ["b1", "b2"]]
    assert all(g.kind == "burst" for g in groups)


def test_exposure_bracket_still_groups():
    t = datetime(2026, 1, 1, 12)
    a = make_scene(3)
    darker = cv2.convertScaleAbs(a, alpha=0.6, beta=0)
    brighter = cv2.convertScaleAbs(a, alpha=1.3, beta=20)
    groups = grouping.compute_groups([_item("n", a, t), _item("d", darker, t + timedelta(seconds=1)),
                                      _item("b", brighter, t + timedelta(seconds=2))])
    assert len(groups) == 1 and len(groups[0].members) == 3


def test_best_shot_prefers_sharpest_frame():
    t = datetime(2026, 1, 1, 12)
    a = make_scene(4)
    items = [_item("soft", cv2.GaussianBlur(a, (0, 0), 2), t, sharp=20),
             _item("sharp", _shift(a, 2, 0), t + timedelta(seconds=1), sharp=90)]
    (g,) = grouping.compute_groups(items)
    assert g.best == "sharp"


def test_duplicates_found_across_the_shoot():
    a = make_scene(5)
    items = [_item("orig", a, datetime(2026, 1, 1, 9)), _item("other", make_scene(6), datetime(2026, 1, 1, 10)),
             _item("copy", a.copy(), None)]
    groups = grouping.compute_groups(items, across_shoot=True)
    assert [sorted(g.members) for g in groups] == [["copy", "orig"]]


def test_chain_is_not_merged_into_one_group():
    """A drifts slowly into a totally different scene; the ends must not end up together."""
    t = datetime(2026, 1, 1, 12)
    a, b = make_scene(7), make_scene(8)
    frames = [cv2.addWeighted(a, 1 - k / 6, b, k / 6, 0) for k in range(7)]
    items = [_item(f"f{k}", f, t + timedelta(seconds=k)) for k, f in enumerate(frames)]
    for g in grouping.compute_groups(items):
        assert not ({"f0", "f6"} <= set(g.members))


def test_split_moments():
    t = datetime(2026, 1, 1, 9)
    times = [("a", t), ("b", t + timedelta(minutes=5)), ("c", t + timedelta(hours=2)), ("d", None)]
    ev = grouping.split_moments(times, gap_minutes=45)
    assert ev == {"a": 1, "b": 1, "c": 2, "d": 0}
