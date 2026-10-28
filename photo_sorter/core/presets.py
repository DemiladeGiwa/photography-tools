"""Genre presets for scoring + auto-cull, plus user presets in ~/.photo_sorter/presets.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

PRESETS_DIR = Path.home() / ".photo_sorter"
PRESETS_FILE = PRESETS_DIR / "presets.json"

_OLD_STRATEGY_NAMES = {
    "global_top_n": "top_n",
    "burst_winner_picks": "best_of_groups",
    "diverse_scene_top_n": "top_n",
    "top_percentage": "top_percent",
    "quality_threshold": "threshold",
}


@dataclass(frozen=True)
class CullingPreset:
    name: str
    description: str
    sharpness_weight: int
    exposure_weight: int
    focus_profile: str  # balanced | portrait_subject | landscape_edge | action_burst
    strategy: str  # best_of_groups | top_n | top_percent | threshold
    threshold: float  # min quality
    percentage: float
    blur_threshold: float = 55.0
    is_builtin: bool = True


BUILTIN_PRESETS: Dict[str, CullingPreset] = {
    p.name: p
    for p in [
        CullingPreset("Balanced General", "Even balance of sharpness and exposure. Good default for most shoots.",
                      60, 40, "balanced", "best_of_groups", 65.0, 20.0, 55.0),
        CullingPreset("Portrait & Bokeh", "Judges sharpness on the subject, so soft backgrounds aren't penalised.",
                      70, 30, "portrait_subject", "best_of_groups", 62.0, 25.0, 55.0),
        CullingPreset("Landscape & Architecture", "Wants edge-to-edge sharpness and protects highlights.",
                      50, 50, "landscape_edge", "threshold", 72.0, 15.0, 60.0),
        CullingPreset("Sports & Action", "Sharpness first; keeps the crispest frame of every burst.",
                      80, 20, "action_burst", "best_of_groups", 60.0, 20.0, 60.0),
        CullingPreset("Events & Low Light", "Tolerates grain and dark scenes; protects shadow detail.",
                      55, 45, "balanced", "best_of_groups", 55.0, 20.0, 45.0),
    ]
}


def _from_dict(d: dict) -> Optional[CullingPreset]:
    try:
        d = dict(d)
        d["strategy"] = _OLD_STRATEGY_NAMES.get(d.get("strategy", ""), d.get("strategy", "best_of_groups"))
        d["is_builtin"] = False
        allowed = CullingPreset.__dataclass_fields__.keys()
        return CullingPreset(**{k: v for k, v in d.items() if k in allowed})
    except TypeError:
        return None


def load_all_presets() -> Dict[str, CullingPreset]:
    presets = dict(BUILTIN_PRESETS)
    if PRESETS_FILE.exists():
        try:
            data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            for name, d in data.items():
                p = _from_dict(d)
                if p:
                    presets[name] = p
        except (OSError, json.JSONDecodeError):
            pass
    return presets


def get_preset(name: str) -> Optional[CullingPreset]:
    return load_all_presets().get(name)


def save_custom_preset(preset: CullingPreset) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8")) if PRESETS_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    d = asdict(preset)
    d["is_builtin"] = False
    data[preset.name] = d
    PRESETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def delete_custom_preset(name: str) -> bool:
    if name in BUILTIN_PRESETS or not PRESETS_FILE.exists():
        return False
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        if name in data:
            del data[name]
            PRESETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
    except (OSError, json.JSONDecodeError):
        pass
    return False
