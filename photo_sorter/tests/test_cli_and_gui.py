import os
import sys

import pytest

from photo_sorter.__main__ import main


def test_cli_analyze_cull_organize_undo(shoot, tmp_path, capsys):
    folder, truth = shoot
    main(["analyze", str(folder), "--csv", str(tmp_path / "a.csv")])
    out = capsys.readouterr().out
    assert f"{len(truth)} photos" in out and (tmp_path / "a.csv").exists()
    main(["cull", str(folder), "--apply"])
    assert "Saved" in capsys.readouterr().out
    main(["organize", str(folder), "--folders", "{flag}"])
    assert "Preview only" in capsys.readouterr().out
    main(["organize", str(folder), "--folders", "{flag}", "--yes"])
    assert (folder / "Picks").is_dir()
    main(["undo", str(folder)])
    assert "Undid" in capsys.readouterr().out
    assert len(list(folder.glob("*.JPG"))) == len(truth)


def test_gui_welcome_screen_without_folder(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display available")
    import photo_sorter.gui.theme as theme
    monkeypatch.setattr(theme, "SETTINGS_PATH", tmp_path / "settings.json")
    from photo_sorter.gui.app import App

    app = App(None)
    app.update()
    assert app._shown_frame is app.views["welcome"]
    app.switch_view("export")
    app.update()
    assert app._shown_frame is app.views["welcome"]  # needs a folder
    app.switch_view("import")
    app.update()
    assert app._shown_frame is app.views["import"]
    app.on_close()


def test_gui_starts_and_loads(shoot, monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError:
        pytest.skip("no display available")
    import photo_sorter.gui.theme as theme
    monkeypatch.setattr(theme, "SETTINGS_PATH", tmp_path / "settings.json")
    from photo_sorter.gui.app import App

    folder, truth = shoot
    app = App(folder)
    deadline = 400
    while deadline and (app.lib is None or app.busy or app.lib.analyzed_count() < len(truth)):
        app.update()
        app.after(50)
        deadline -= 1
    app.update()
    assert app.lib is not None and app.lib.analyzed_count() == len(truth)
    for v in ("import", "insights", "groups", "auto", "organize", "export", "history", "cull"):
        app.switch_view(v)
        app.update()
    cull = app.views["cull"]
    assert len(cull.visible) == len(truth)
    cull.grid.select(cull.visible[0])
    app.decide_flag("pick")
    assert cull.visible[0].flag == "pick"
    app.undo_decision()
    assert cull.visible[0].flag is None
    app.open_viewer(cull.visible[1])
    app.update()
    app.decide_flag("reject")  # loupe flash animation path
    app.update()
    app.close_viewer()
    cull.show_similar(cull.visible[0])
    app.update()
    assert cull.similar_to is not None
    cull.clear_similar()
    app.show_settings()
    app.update()
    app.on_close()
