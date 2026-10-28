from pathlib import Path

from photo_sorter.core import export
from photo_sorter.core.html_report import generate_html_report
from photo_sorter.core.library import FLAG_PICK, FLAG_REJECT, Library

from .conftest import make_scene, write_jpeg


def test_export_picks_with_raw_pair_and_xmp(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    write_jpeg(src / "A.JPG", make_scene(1))
    (src / "A.ARW").write_bytes(b"raw")
    write_jpeg(src / "B.JPG", make_scene(2))
    lib = Library(src)
    lib.scan()
    lib.analyze()
    a = next(p for p in lib.photos if p.stem == "A")
    lib.set_flag([a], FLAG_PICK)
    lib.set_rating([a], 4)
    dest = tmp_path / "out"
    n, journal, errors = export.export_photos(lib, export.select(lib, "picks"),
                                              export.ExportOptions(destination=dest, write_xmp=True))
    assert n == 1 and not errors
    assert sorted(p.name for p in dest.iterdir()) == ["A.ARW", "A.JPG", "A.xmp"]
    assert (src / "A.ARW").exists()  # copy, not move
    lib.close()


def test_rejects_to_folder_and_reports(shoot, tmp_path):
    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    lib.set_flag(lib.photos[:2], FLAG_REJECT)
    n, journal, errors = export.handle_rejects(lib, "folder")
    assert n == 2 and (folder / "_Rejected").exists()
    assert len(list((folder / "_Rejected").iterdir())) == 2
    export.write_csv(lib.photos, tmp_path / "r.csv")
    assert (tmp_path / "r.csv").read_text(encoding="utf-8-sig").startswith("File,Folder")
    out = generate_html_report(lib, lib.photos, tmp_path / "sheet.html", title="Test")
    html = out.read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in html and "Test" in html
    assert "IMG_0003" in export.lightroom_filter_text(lib.photos)
    lib.close()


def _pair_lib(tmp_path, monkeypatch):
    from photo_sorter.core import file_operations as fo

    monkeypatch.setattr(fo, "send_to_trash", lambda p: False)  # no Recycle Bin in tests -> fallback folder
    src = tmp_path / "src"
    src.mkdir()
    write_jpeg(src / "A.JPG", make_scene(1))
    (src / "A.ARW").write_bytes(b"raw-a")
    write_jpeg(src / "B.JPG", make_scene(2))
    lib = Library(src)
    lib.scan()
    lib.analyze()
    return src, lib


def test_copy_then_delete_originals_only_when_verified(tmp_path, monkeypatch):
    src, lib = _pair_lib(tmp_path, monkeypatch)
    photos = lib.photos
    dest = tmp_path / "out"
    # RAW not included -> photo A has no copy of its RAW, so it must be kept
    n, journal, _ = export.export_photos(lib, photos, export.ExportOptions(destination=dest, include_raw=False))
    copies = export.exported_copies_from_journal(journal)
    deleted, _j, kept = export.delete_originals(lib, photos, copies=copies)
    assert deleted == 1 and len(kept) == 1
    assert (src / "A.ARW").exists() and (src / "A.JPG").exists()
    assert not (src / "B.JPG").exists()
    assert (dest / "B.JPG").exists()
    lib.close()


def test_find_and_delete_already_exported(tmp_path, monkeypatch):
    import shutil

    src, lib = _pair_lib(tmp_path, monkeypatch)
    dest = tmp_path / "out" / "sub"
    dest.mkdir(parents=True)
    shutil.copy2(src / "A.JPG", dest / "A.JPG")
    shutil.copy2(src / "A.ARW", dest / "A.ARW")
    (dest / "B.JPG").write_bytes(b"different size")
    found = export.find_exported(lib, tmp_path / "out")
    assert [p.stem for p in found] == ["A"]
    deleted, journal, problems = export.delete_originals(lib, found, destination=tmp_path / "out")
    assert deleted == 1 and not problems
    assert not (src / "A.JPG").exists() and not (src / "A.ARW").exists()
    assert (src / "B.JPG").exists()
    lib.close()


def test_web_export_resizes_and_watermarks(shoot, tmp_path):
    from PIL import Image

    from photo_sorter.core import web_export

    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    opts = web_export.WebExportOptions(destination=tmp_path / "web", long_edge=400, watermark="© Test")
    n, journal, errors = web_export.export_web(lib, lib.photos[:2], opts)
    assert n == 2 and not errors
    outs = sorted((tmp_path / "web").glob("*.jpg"))
    assert len(outs) == 2
    with Image.open(outs[0]) as im:
        assert max(im.size) == 400
    # undo removes the generated files
    from photo_sorter.core import file_operations as fo

    fo.undo_journal(journal, folder)
    assert not list((tmp_path / "web").glob("*.jpg"))
    lib.close()


def test_save_as_jpg_full_size(shoot, tmp_path):
    from PIL import Image

    from photo_sorter.core import web_export

    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    n, journal, errors = web_export.save_as_jpg(lib, lib.photos[:2], tmp_path / "jpgs")
    assert n == 2 and not errors
    outs = sorted((tmp_path / "jpgs").glob("*.jpg"))
    assert len(outs) == 2
    with Image.open(outs[0]) as im, Image.open(lib.photos[0].display) as src:
        assert im.size == src.size
    assert lib.photos[0].display.exists()


def test_parallel_jpeg_export_keeps_order_names_and_stop(shoot, tmp_path):
    from photo_sorter.core import web_export

    folder, truth = shoot
    lib = Library(folder)
    lib.scan()
    photos = lib.photos
    opts = web_export.WebExportOptions(destination=tmp_path / "web", long_edge=300, rename_template="Shoot")
    n, journal, errors = web_export.export_web(lib, photos, opts, max_workers=4)
    assert n == len(photos) and not errors
    # same names a one-at-a-time run gives, in photo order
    expected = ["Shoot.jpg"] + [f"Shoot ({i}).jpg" for i in range(1, len(photos))]
    assert [Path(e.dst).name for e in journal.entries] == expected
    assert [Path(e.src) for e in journal.entries] == [p.display for p in photos]

    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    opts2 = web_export.WebExportOptions(destination=tmp_path / "web2", long_edge=300)
    n2, journal2, errors2 = web_export.export_web(lib, photos, opts2, should_stop=stop_after_two, max_workers=1)
    assert n2 == 2 and not errors2 and len(list((tmp_path / "web2").glob("*.jpg"))) == 2
    lib.close()


def test_write_xmp_reports_progress_and_stops(tmp_path):
    for i in range(4):
        write_jpeg(tmp_path / f"P{i}.JPG", make_scene(i))
    write_jpeg(tmp_path / "undecided.JPG", make_scene(9))
    lib = Library(tmp_path)
    lib.scan()
    decided = [p for p in lib.photos if p.stem != "undecided"]
    lib.set_flag(decided, FLAG_PICK)
    seen = []
    n, errors = export.write_xmp_for(lib.photos, pick_label="Green", progress_cb=lambda d, t, name: seen.append((d, t)))
    assert n == 4 and not errors and seen[-1] == (4, 4)
    assert not (tmp_path / "undecided.xmp").exists()
    for f in tmp_path.glob("*.xmp"):
        f.unlink()
    n, _ = export.write_xmp_for(lib.photos, should_stop=lambda: True)
    assert n == 0 and not list(tmp_path.glob("*.xmp"))
    lib.close()
