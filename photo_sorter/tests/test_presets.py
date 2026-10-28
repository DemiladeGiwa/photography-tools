import json

from photo_sorter.core import presets


def test_builtin_presets_valid():
    for p in presets.BUILTIN_PRESETS.values():
        assert p.strategy in ("best_of_groups", "top_n", "top_percent", "threshold")
        assert 0 <= p.blur_threshold <= 100


def test_custom_preset_lifecycle_and_legacy_names(tmp_path, monkeypatch):
    monkeypatch.setattr(presets, "PRESETS_DIR", tmp_path)
    monkeypatch.setattr(presets, "PRESETS_FILE", tmp_path / "presets.json")
    # a preset saved by the old version, with an old strategy name and no blur_threshold
    (tmp_path / "presets.json").write_text(json.dumps({"Old": {
        "name": "Old", "description": "", "sharpness_weight": 70, "exposure_weight": 30,
        "focus_profile": "balanced", "strategy": "burst_winner_picks", "threshold": 60, "percentage": 10}}))
    old = presets.get_preset("Old")
    assert old.strategy == "best_of_groups" and old.blur_threshold == 55
    presets.save_custom_preset(presets.CullingPreset("Mine", "d", 50, 50, "balanced", "top_n", 70, 20, 60, False))
    assert "Mine" in presets.load_all_presets()
    assert presets.delete_custom_preset("Mine")
    assert not presets.delete_custom_preset("Balanced General")
