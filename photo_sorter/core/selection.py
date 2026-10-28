"""
Auto-cull: turn analysis into pick / reject suggestions.

``plan_auto_cull`` is pure - it returns a plan that the UI can preview
(counts + per-photo reasons) before ``apply_plan`` writes the flags.
Photos the user has already decided by hand are left alone unless
``respect_manual`` is turned off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .library import FLAG_PICK, FLAG_REJECT, Library, Photo

STRATEGIES = {
    "best_of_groups": "Best of each burst/group + strong singles",
    "top_n": "Top N photos",
    "top_percent": "Top % of the shoot",
    "threshold": "Everything above a quality score",
}


@dataclass
class CullOptions:
    strategy: str = "best_of_groups"
    count: int = 50  # top_n
    percent: float = 20.0  # top_percent
    min_quality: float = 65.0  # threshold / singles in best_of_groups
    max_per_group: int = 1  # diversity limit for top_n / top_percent (0 = unlimited)
    reject_blurry: bool = True  # reject frames classified "Blurry"
    reject_group_extras: bool = True  # reject non-best frames of a group
    reject_unpicked: bool = False  # everything not picked becomes rejected
    respect_manual: bool = True
    pick_rating: int = 0  # stars to give picks (0 = don't change)


@dataclass
class CullPlan:
    flags: Dict[str, Optional[str]] = field(default_factory=dict)
    reasons: Dict[str, str] = field(default_factory=dict)
    rating: Dict[str, int] = field(default_factory=dict)

    @property
    def picks(self) -> int:
        return sum(1 for f in self.flags.values() if f == FLAG_PICK)

    @property
    def rejects(self) -> int:
        return sum(1 for f in self.flags.values() if f == FLAG_REJECT)

    @property
    def cleared(self) -> int:
        return sum(1 for f in self.flags.values() if f is None)


def plan_auto_cull(lib: Library, opts: CullOptions, scope: Optional[Sequence[Photo]] = None) -> CullPlan:
    photos = [p for p in (scope if scope is not None else lib.photos) if p.analyzed]
    editable = [p for p in photos if not (opts.respect_manual and p.touched)]
    editable_keys = {p.key for p in editable}
    plan = CullPlan()

    def set_flag(p: Photo, flag: Optional[str], why: str) -> None:
        if p.key in editable_keys:
            plan.flags[p.key] = flag
            plan.reasons[p.key] = why
            if flag == FLAG_PICK and opts.pick_rating:
                plan.rating[p.key] = opts.pick_rating

    # Start from a clean slate for editable photos.
    for p in editable:
        plan.flags[p.key] = None
        plan.reasons[p.key] = "Not selected"

    hard_blurry = {p.key for p in photos if opts.reject_blurry and p.blur and p.blur.label == "Blurry"}
    picks: List[Photo] = []

    if opts.strategy == "best_of_groups":
        for g in lib.groups:
            members = [lib.by_key[k] for k in g.members if k in lib.by_key]
            members = [m for m in members if m in photos]
            if not members:
                continue
            best = lib.by_key.get(g.best) if g.best in lib.by_key else None
            if best is None or best not in members:
                best = max(members, key=lambda m: m.quality_score)
            if best.key not in hard_blurry:
                picks.append(best)
                set_flag(best, FLAG_PICK, f"Best of {g.kind} group #{g.id} ({len(members)} frames)")
            for m in members:
                if m is best:
                    continue
                if opts.reject_group_extras:
                    set_flag(m, FLAG_REJECT, f"Weaker frame in {g.kind} group #{g.id}")
        for p in photos:
            if p.group_id is None and p.key not in hard_blurry and p.quality_score >= opts.min_quality:
                picks.append(p)
                set_flag(p, FLAG_PICK, f"Strong single (quality {p.quality_score:.0f})")
    else:
        ranked = sorted((p for p in photos if p.key not in hard_blurry), key=lambda p: -p.quality_score)
        if opts.strategy == "threshold":
            chosen = [p for p in ranked if p.quality_score >= opts.min_quality]
            limit = len(chosen)
            ranked = chosen
        elif opts.strategy == "top_percent":
            limit = max(1, int(math.ceil(len(photos) * opts.percent / 100.0)))
        else:
            limit = max(0, int(opts.count))
        per_group: Dict[int, int] = {}
        # Manual picks that we must keep count toward the budget.
        manual_picks = [p for p in photos if p.key not in editable_keys and p.flag == FLAG_PICK]
        budget = max(0, limit - len(manual_picks))
        for p in manual_picks:
            if p.group_id is not None:
                per_group[p.group_id] = per_group.get(p.group_id, 0) + 1
        for p in ranked:
            if len(picks) >= budget:
                break
            if p.key not in editable_keys:
                continue
            if opts.max_per_group and p.group_id is not None:
                if per_group.get(p.group_id, 0) >= opts.max_per_group:
                    continue
                per_group[p.group_id] = per_group.get(p.group_id, 0) + 1
            picks.append(p)
            set_flag(p, FLAG_PICK, f"Rank {len(picks)} by quality ({p.quality_score:.0f})")
        if opts.reject_group_extras:
            picked = {p.key for p in picks}
            for g in lib.groups:
                if any(k in picked for k in g.members):
                    for k in g.members:
                        if k not in picked and k in lib.by_key:
                            set_flag(lib.by_key[k], FLAG_REJECT, f"Similar to a pick in group #{g.id}")

    for k in hard_blurry:
        p = lib.by_key[k]
        set_flag(p, FLAG_REJECT, f"Blurry (sharpness {p.sharpness_score:.0f}, threshold {lib.settings.blur_threshold:.0f})")

    if opts.reject_unpicked:
        for p in editable:
            if plan.flags.get(p.key) is None:
                set_flag(p, FLAG_REJECT, "Not picked")
    return plan


def apply_plan(lib: Library, plan: CullPlan) -> None:
    for key, flag in plan.flags.items():
        p = lib.by_key.get(key)
        if p is None:
            continue
        p.flag = flag
        if key in plan.rating:
            p.rating = plan.rating[key]
    lib.mark_dirty()
