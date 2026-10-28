from photo_sorter.core import selection
from photo_sorter.core.library import FLAG_PICK, FLAG_REJECT, Library


def _lib(folder):
    lib = Library(folder)
    lib.scan()
    lib.analyze()
    return lib


def test_best_of_groups(shoot):
    folder, truth = shoot
    lib = _lib(folder)
    plan = selection.plan_auto_cull(lib, selection.CullOptions(strategy="best_of_groups", min_quality=50))
    selection.apply_plan(lib, plan)
    picks = [p.name for p in lib.photos if p.flag == FLAG_PICK]
    assert len(picks) == 3  # best of 2 bursts + the single
    assert all(truth[n][1] == "sharp" for n in picks)
    blurry = [p for p in lib.photos if truth[p.name][1] == "blur"]
    assert all(p.flag == FLAG_REJECT for p in blurry)
    lib.close()


def test_manual_decisions_are_respected(shoot):
    folder, truth = shoot
    lib = _lib(folder)
    blur = next(p for p in lib.photos if truth[p.name][1] == "blur")
    lib.set_flag([blur], FLAG_PICK)  # user insists
    plan = selection.plan_auto_cull(lib, selection.CullOptions())
    assert blur.key not in plan.flags
    plan2 = selection.plan_auto_cull(lib, selection.CullOptions(respect_manual=False))
    assert plan2.flags[blur.key] == FLAG_REJECT
    lib.close()


def test_top_n_with_diversity(shoot):
    folder, _ = shoot
    lib = _lib(folder)
    plan = selection.plan_auto_cull(lib, selection.CullOptions(strategy="top_n", count=3, max_per_group=1,
                                                               reject_group_extras=False, reject_blurry=False))
    picked = [lib.by_key[k] for k, f in plan.flags.items() if f == FLAG_PICK]
    assert len(picked) == 3
    groups = [p.group_id for p in picked if p.group_id]
    assert len(groups) == len(set(groups))  # never two from the same burst
    lib.close()


def test_threshold_and_percent(shoot):
    folder, truth = shoot
    lib = _lib(folder)
    plan = selection.plan_auto_cull(lib, selection.CullOptions(strategy="top_percent", percent=50, max_per_group=0,
                                                               reject_group_extras=False))
    assert plan.picks == 3
    plan = selection.plan_auto_cull(lib, selection.CullOptions(strategy="threshold", min_quality=101))
    assert plan.picks == 0
    lib.close()
