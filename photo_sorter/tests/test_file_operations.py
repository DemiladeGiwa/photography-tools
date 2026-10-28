from pathlib import Path

from photo_sorter.core import file_operations as fo


def test_collision_safe_path(tmp_path):
    (tmp_path / "a.jpg").write_text("x")
    assert fo.collision_safe_path(tmp_path / "a.jpg").name == "a (1).jpg"
    assert fo.collision_safe_path(tmp_path / "b.jpg").name == "b.jpg"


def test_move_with_sidecars_and_undo(tmp_path):
    src = tmp_path / "IMG_1.CR2"
    src.write_text("raw")
    (tmp_path / "IMG_1.xmp").write_text("<xmp/>")
    (tmp_path / "IMG_1.CR2.xmp").write_text("<dt/>")
    with fo.Transaction(tmp_path, "test") as tx:
        dst = tx.move(src, tmp_path / "sub" / "Renamed.CR2")
    assert dst == tmp_path / "sub" / "Renamed.CR2" and dst.exists()
    assert (tmp_path / "sub" / "Renamed.xmp").exists()
    assert (tmp_path / "sub" / "Renamed.CR2.xmp").exists()
    restored, errors = fo.undo_journal(tx.journal, tmp_path)
    assert not errors and restored == 3
    assert src.exists() and (tmp_path / "IMG_1.xmp").exists()
    assert not (tmp_path / "sub").exists()  # emptied folder removed on undo


def test_never_overwrites(tmp_path):
    (tmp_path / "a.jpg").write_text("A")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.jpg").write_text("existing")
    with fo.Transaction(tmp_path, "t") as tx:
        dst = tx.move(tmp_path / "a.jpg", tmp_path / "d" / "a.jpg")
    assert dst.name == "a (1).jpg"
    assert (tmp_path / "d" / "a.jpg").read_text() == "existing"


def test_copy_undo_removes_copy(tmp_path):
    (tmp_path / "a.jpg").write_text("A")
    with fo.Transaction(tmp_path, "t") as tx:
        c = tx.copy(tmp_path / "a.jpg", tmp_path / "out" / "a.jpg")
    assert c.exists()
    fo.undo_journal(tx.journal, tmp_path)
    assert not c.exists() and (tmp_path / "a.jpg").exists()


def test_journal_roundtrip(tmp_path):
    (tmp_path / "a.jpg").write_text("A")
    with fo.Transaction(tmp_path, "organize", "label here") as tx:
        tx.move(tmp_path / "a.jpg", tmp_path / "x" / "a.jpg")
    paths = fo.list_journals(tmp_path)
    assert len(paths) == 1
    j = fo.Journal.load(paths[0])
    assert j.label == "label here" and j.done_count == 1 and j.undoable


def test_trash_falls_back_to_rejected_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(fo, "send_to_trash", lambda p: False)
    (tmp_path / "bad.jpg").write_text("x")
    with fo.Transaction(tmp_path, "rejects") as tx:
        assert tx.trash(tmp_path / "bad.jpg")
    assert (tmp_path / "_Rejected" / "bad.jpg").exists()  # never permanently deleted


def test_xmp_uses_lightroom_name_and_preserves_edits(tmp_path):
    raw = tmp_path / "IMG_9.CR2"
    raw.write_text("raw")
    side = tmp_path / "IMG_9.xmp"
    side.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                    '<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" '
                    'crs:Exposure2012="+0.50" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="1"/>'
                    '</rdf:RDF></x:xmpmeta>')
    out = fo.write_xmp(raw, 5, "Green")
    assert out == side
    text = side.read_text()
    assert 'crs:Exposure2012="+0.50"' in text
    assert 'xmp:Rating="5"' in text and 'xmp:Label="Green"' in text
    assert fo.read_xmp(raw) == (5, "Green")
    fo.write_xmp(raw, -1, None)
    assert fo.read_xmp(raw) == (-1, None)


def test_new_xmp_created(tmp_path):
    img = tmp_path / "DSC_1.NEF"
    img.write_text("raw")
    p = fo.write_xmp(img, 3, None)
    assert p.name == "DSC_1.xmp"
    assert fo.read_xmp(img) == (3, None)
