import json
import shutil

from photo_sorter.core import file_operations as fo
from photo_sorter.core.constants import CACHE_DIRNAME
from photo_sorter.core.library import FLAG_PICK, FLAG_REJECT, Library, PhotoFilter, discover, filter_and_sort

from .conftest import make_scene, write_jpeg


def test_raw_jpeg_pairing_and_display_choice(tmp_path):
    write_jpeg(tmp_path / "IMG_1.JPG", make_scene(1))
    (tmp_path / "IMG_1.CR2").write_bytes(b"fake raw")
    write_jpeg(tmp_path / "IMG_2.JPG", make_scene(2))
    photos = discover(tmp_path)
    assert len(photos) == 2
    pair = next(p for p in photos if p.stem == "IMG_1")
    assert pair.master.suffix == ".CR2" and pair.display.suffix == ".JPG"
    assert pair.type_label == "RAW+JPG" and pair.has_raw


def test_recursive_scan_skips_cache_and_rejected(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "_Rejected").mkdir()
    (tmp_path / CACHE_DIRNAME).mkdir()
    write_jpeg(tmp_path / "top.jpg", make_scene(1))
    write_jpeg(tmp_path / "a" / "sub.jpg", make_scene(2))
    write_jpeg(tmp_path / "_Rejected" / "bad.jpg", make_scene(3))
    write_jpeg(tmp_path / CACHE_DIRNAME / "x.jpg", make_scene(4))
    assert {p.name for p in discover(tmp_path, recursive=False)} == {"top.jpg"}
    assert {p.name for p in discover(tmp_path, recursive=True)} == {"top.jpg", "sub.jpg"}


def test_analysis_scores_and_groups(shoot):
    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    run = lib.analyze()
    assert run.done == len(truth) and not run.errors
    for p in lib.photos:
        scene, kind = truth[p.name]
        assert p.is_blurry == (kind == "blur"), p.name
    assert len(lib.groups) == 2
    best = {lib.by_key[g.best].name for g in lib.groups}
    assert all(truth[n][1] == "sharp" for n in best)
    lib.close()


def test_decisions_persist_and_cache_makes_reopen_instant(shoot):
    folder, _ = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    p0, p1 = lib.photos[0], lib.photos[1]
    lib.set_flag([p0], FLAG_PICK)
    lib.set_rating([p1], 4)
    lib.set_label([p1], "Red")
    lib.close()

    lib2 = Library(folder)
    lib2.scan()
    assert lib2.analyzed_count() == len(lib2.photos)  # restored from cache, no decoding
    assert not lib2.pending_paths()
    q0, q1 = lib2.by_key[p0.key], lib2.by_key[p1.key]
    assert q0.flag == FLAG_PICK and q0.touched
    assert q1.rating == 4 and q1.label == "Red"
    assert len(lib2.groups) == 2
    lib2.close()


def test_existing_xmp_ratings_are_imported(tmp_path):
    write_jpeg(tmp_path / "a.jpg", make_scene(1))
    (tmp_path / "b.NEF").write_bytes(b"raw")
    fo.write_xmp(tmp_path / "a.jpg", 3, "Blue")
    fo.write_xmp(tmp_path / "b.NEF", -1, None)
    lib = Library(tmp_path)
    lib.scan()
    a = next(p for p in lib.photos if p.name == "a.jpg")
    b = next(p for p in lib.photos if p.name == "b.NEF")
    assert a.rating == 3 and a.label == "Blue"
    assert b.flag == FLAG_REJECT
    lib.close()


def test_unreadable_file_does_not_break_scan(tmp_path):
    write_jpeg(tmp_path / "ok.jpg", make_scene(1))
    (tmp_path / "broken.jpg").write_bytes(b"not an image at all")
    lib = Library(tmp_path)
    lib.scan()
    run = lib.analyze()
    assert run.done == 2 and len(run.errors) == 1
    broken = next(p for p in lib.photos if p.name == "broken.jpg")
    assert broken.error and not broken.analyzed
    lib.close()


def test_filter_and_sort(shoot):
    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    lib.set_flag(lib.photos[:2], FLAG_REJECT)
    assert len(filter_and_sort(lib.photos, PhotoFilter(flag="rejects"))) == 2
    assert len(filter_and_sort(lib.photos, PhotoFilter(flag="not_rejected"))) == len(truth) - 2
    blurry = filter_and_sort(lib.photos, PhotoFilter(only_blurry=True))
    assert [p.name for p in blurry] == [n for n, (s, k) in truth.items() if k == "blur"]
    collapsed = filter_and_sort(lib.photos, PhotoFilter(hide_non_best=True))
    assert len(collapsed) == 3  # one per scene
    ranked = filter_and_sort(lib.photos, PhotoFilter(), "Quality (best first)")
    assert ranked[-1].is_blurry
    lib.close()


def test_cache_get_many_matches_single_lookups(shoot):
    folder, _ = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    paths = [p.display for p in lib.photos] + [folder / "missing.jpg"]
    many = lib.cache.get_many(paths)
    assert set(many) == {str(p.display) for p in lib.photos}
    for p in lib.photos:
        assert many[str(p.display)] == lib.cache.get(p.display)
    lib.close()


def test_group_edits_survive_reopen(shoot):
    folder, _ = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    lib.save_state(force=True)
    g = max(lib.groups, key=lambda g: len(g.members))
    removed = next(k for k in g.members if k != g.best)
    lib.remove_from_group(removed)
    assert lib.dirty
    lib.close()

    lib2 = Library(folder)
    lib2.scan()
    assert lib2.by_key[removed].group_id is None
    lib2.close()
