import shutil
from datetime import datetime

from photo_sorter.core import importer, stats
from photo_sorter.core.library import FLAG_PICK, Library

from .conftest import make_scene, write_jpeg


def _card(tmp_path):
    card = tmp_path / "card" / "DCIM" / "100CANON"
    card.mkdir(parents=True)
    write_jpeg(card / "IMG_1.JPG", make_scene(1), datetime(2026, 5, 1, 9))
    (card / "IMG_1.CR2").write_bytes(b"raw-bytes")
    write_jpeg(card / "IMG_2.JPG", make_scene(2), datetime(2026, 5, 2, 18))
    (card / "MVI_3.MP4").write_bytes(b"video")
    return tmp_path / "card"


def test_import_sorts_by_date_keeps_pairs_and_skips_duplicates(tmp_path):
    card = _card(tmp_path)
    lib = tmp_path / "Pictures"
    plan = importer.plan_import(card, lib)
    assert len(plan.units) == 3 and plan.duplicates == 0
    n, journal, errors = importer.apply_import(plan)
    assert n == 3 and not errors
    day1 = lib / "2026" / "2026-05-01"
    assert sorted(p.name for p in day1.iterdir()) == ["IMG_1.CR2", "IMG_1.JPG"]
    assert (lib / "2026" / "2026-05-02" / "IMG_2.JPG").exists()
    assert (card / "DCIM" / "100CANON" / "IMG_1.CR2").exists()  # card untouched
    again = importer.plan_import(card, lib)
    assert again.duplicates == 3 and not again.to_copy


def test_import_rename_and_no_videos(tmp_path):
    card = _card(tmp_path)
    plan = importer.plan_import(card, tmp_path / "L", "{date}", "{YYYY}{MM}{DD}_{seq:3}", include_videos=False)
    importer.apply_import(plan)
    names = sorted(p.name for p in (tmp_path / "L" / "2026-05-01").iterdir())
    assert names == ["20260501_001.CR2", "20260501_001.JPG"]
    assert not any(p.suffix == ".MP4" for p in (tmp_path / "L").rglob("*"))


def test_stats_and_find_similar(shoot):
    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    lib.set_flag([lib.photos[0]], FLAG_PICK)
    s = stats.compute(lib.photos, len(lib.groups))
    assert s.total == len(truth) and s.picks == 1 and s.groups == 2
    assert sum(b.count for b in s.quality_hist) == len(truth)
    assert s.cameras and s.cameras[0].label == "TestCam"
    target = next(p for p in lib.photos if p.name == "IMG_0001.JPG")
    sims = [p.name for _s, p in lib.find_similar(target)]
    assert sims[:2] and set(sims[:2]) <= {"IMG_0002.JPG", "IMG_0003.JPG"}
    assert "IMG_0006.JPG" not in sims[:2]
    lib.close()
