from datetime import datetime
from pathlib import Path

from photo_sorter.core import file_operations as fo
from photo_sorter.core import organizer
from photo_sorter.core.library import FLAG_PICK, FLAG_REJECT, Library

from .conftest import make_scene, write_jpeg


def _lib(folder, recursive=False):
    lib = Library(folder, recursive=recursive)
    lib.scan()
    lib.analyze()
    return lib


def test_render_tokens(shoot):
    folder, _ = shoot
    lib = _lib(folder)
    p = lib.photos[0]
    p.flag, p.rating, p.label = FLAG_PICK, 3, "Red"
    ctx = organizer.TemplateContext(events={p.key: 2}, seq={p.key: 7})
    assert organizer.render("{YYYY}-{MM}-{DD} {hh}{mm}", p, ctx) == "2026-09-20 1000"
    assert organizer.render("{flag}|{rating}|{label}|{event}|{seq:3}|{camera}", p, ctx) == \
        "Picks|3 stars|Red|02|007|TestCam"
    assert organizer.render_folder("{YYYY}/../{MonthName}", p, ctx) == Path("2026") / "September"
    lib.close()


def test_sanitize_and_validate():
    assert organizer.sanitize_component('a:b*c?"d') == "a_b_c__d"
    assert organizer.sanitize_component("CON") == "_CON"
    assert organizer.validate_template("{YYYY}/{nope}") == ["nope"]


def test_plan_apply_and_undo(shoot):
    folder, truth = shoot
    lib = _lib(folder)
    plan = organizer.plan_organize(lib, lib.photos, "{date} Event {event}", "{YYYY}{MM}{DD}_{seq:3}")
    assert plan.folders() == {"2026-09-20 Event 01": len(truth)}  # 10-minute gaps < 45 min
    journal, errors = organizer.apply_plan(lib, plan, remove_empty_folders=True)
    assert not errors
    moved = sorted(p.name for p in (folder / "2026-09-20 Event 01").iterdir())
    assert moved[0] == "20260920_001.JPG" and len(moved) == len(truth)
    fo.undo_journal(journal, folder)
    assert sorted(p.name for p in folder.glob("*.JPG")) == sorted(truth)
    assert not (folder / "2026-09-20 Event 01").exists()
    lib.close()


def test_decisions_follow_renamed_files(shoot):
    folder, _ = shoot
    lib = _lib(folder)
    lib.set_flag([lib.photos[0]], FLAG_PICK)
    lib.set_flag([lib.photos[1]], FLAG_REJECT)
    lib.save_state()
    plan = organizer.plan_organize(lib, lib.photos, "{flag}", "renamed_{seq:2}")
    journal, _ = organizer.apply_plan(lib, plan)
    lib.migrate_keys(journal)
    lib.close()
    lib2 = Library(folder, recursive=True)
    lib2.scan()
    picks = [p for p in lib2.photos if p.flag == FLAG_PICK]
    rejects = [p for p in lib2.photos if p.flag == FLAG_REJECT]
    assert len(picks) == 1 and picks[0].rel_dir == "Picks" and picks[0].name.startswith("renamed_")
    assert len(rejects) == 1 and rejects[0].rel_dir == "Rejects"
    assert lib2.analyzed_count() == len(lib2.photos)  # cache followed the renames
    lib2.close()


def test_pairs_move_together_and_flatten(tmp_path):
    (tmp_path / "card1").mkdir()
    (tmp_path / "card2").mkdir()
    write_jpeg(tmp_path / "card1" / "IMG_1.JPG", make_scene(1), datetime(2026, 1, 1, 9))
    (tmp_path / "card1" / "IMG_1.CR2").write_bytes(b"raw")
    (tmp_path / "card1" / "IMG_1.xmp").write_text("<x/>")
    write_jpeg(tmp_path / "card2" / "IMG_1.JPG", make_scene(2), datetime(2026, 1, 1, 10))  # same name!
    lib = _lib(tmp_path, recursive=True)
    plan = organizer.plan_flatten(lib)
    assert plan.collisions >= 1
    journal, errors = organizer.apply_plan(lib, plan, remove_empty_folders=True)
    assert not errors
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert "IMG_1.CR2" in names and "IMG_1.xmp" in names
    assert any(n.startswith("IMG_1 (1)") for n in names)  # clash renamed, not overwritten
    assert not (tmp_path / "card1").exists() and not (tmp_path / "card2").exists()
    lib.close()


def test_copy_to_other_folder_leaves_originals(shoot, tmp_path):
    folder, truth = shoot
    lib = _lib(folder)
    dest = tmp_path / "out"
    plan = organizer.plan_organize(lib, lib.photos, "{YYYY}", target_root=dest, mode="copy")
    organizer.apply_plan(lib, plan)
    assert len(list((dest / "2026").glob("*.JPG"))) == len(truth)
    assert len(list(folder.glob("*.JPG"))) == len(truth)
    lib.close()
