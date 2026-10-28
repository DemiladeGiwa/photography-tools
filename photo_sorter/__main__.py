"""Command line entry point.

    python -m photo_sorter                     open the app (reopens the last folder)
    python -m photo_sorter "D:/Shoots/Wedding" open the app on a folder
    python -m photo_sorter analyze FOLDER      analyse and print a summary (+ --csv / --html)
    python -m photo_sorter cull FOLDER         suggest picks/rejects (add --apply to save them)
    python -m photo_sorter organize FOLDER --folders "{YYYY}/{date}"   preview (add --yes to run)
    python -m photo_sorter flatten FOLDER      move photos from subfolders back up (add --yes)
    python -m photo_sorter xmp FOLDER          write ratings/labels to XMP sidecars
    python -m photo_sorter undo FOLDER         undo the most recent file operation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__

LEGACY_VIEWS = {"hub": "cull", "selector": "auto", "grouper": "groups", "blur": "cull", "gui": "cull"}
COMMANDS = {"analyze", "cull", "organize", "flatten", "xmp", "undo"}


def _progress(done: int, total: int, name: str) -> None:
    width = 30
    filled = int(width * done / max(1, total))
    sys.stdout.write(f"\r  [{'#' * filled}{'.' * (width - filled)}] {done}/{total}  ")
    sys.stdout.flush()
    if done == total:
        sys.stdout.write("\n")


def _open(folder: str, recursive: bool, analyze: bool = True):
    from .core.library import Library

    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")
    lib = Library(root, recursive=recursive)
    lib.scan()
    if analyze and lib.pending_paths():
        print(f"Analysing {len(lib.pending_paths())} photos…")
        lib.analyze(_progress)
    return lib


def cmd_analyze(a) -> None:
    from .core import export
    from .core.html_report import generate_html_report

    lib = _open(a.folder, a.recursive)
    c = lib.counts()
    print(f"{c['total']} photos · {c['analyzed']} analysed · {c['blurry']} blurry · {c['groups']} groups · "
          f"{c['errors']} unreadable")
    ranked = sorted((p for p in lib.photos if p.quality), key=lambda p: -p.quality_score)
    for p in ranked[: a.top]:
        print(f"  {p.quality_score:5.1f}  S{p.sharpness_score:5.1f}  E{p.exposure_score:5.1f}  {p.name}"
              f"{'  [best of group]' if p.is_best else ''}")
    if a.csv:
        export.write_csv(lib.photos, Path(a.csv))
        print(f"CSV written to {a.csv}")
    if a.html:
        generate_html_report(lib, lib.photos, Path(a.html), title=lib.root.name)
        print(f"Contact sheet written to {a.html}")
    lib.close()


def cmd_cull(a) -> None:
    from .core import presets, selection

    lib = _open(a.folder, a.recursive)
    opts = selection.CullOptions()
    if a.preset:
        p = presets.get_preset(a.preset)
        if not p:
            sys.exit(f"Unknown preset. Choose from: {', '.join(presets.load_all_presets())}")
        lib.settings.sharpness_weight, lib.settings.exposure_weight = p.sharpness_weight, p.exposure_weight
        lib.settings.focus_profile, lib.settings.blur_threshold = p.focus_profile, p.blur_threshold
        lib.recompute()
        opts.strategy, opts.min_quality, opts.percent = p.strategy, p.threshold, p.percentage
    if a.strategy:
        opts.strategy = a.strategy
    if a.count:
        opts.count = a.count
    plan = selection.plan_auto_cull(lib, opts)
    print(f"Suggestion: {plan.picks} picks, {plan.rejects} rejects, {plan.cleared} unflagged.")
    if a.apply:
        selection.apply_plan(lib, plan)
        lib.save_state(force=True)
        print("Saved. Open the folder in the app to review.")
    else:
        print("Dry run - add --apply to save these flags.")
    lib.close()


def cmd_organize(a, flatten: bool = False) -> None:
    from .core import export, organizer

    lib = _open(a.folder, recursive=True if flatten else a.recursive, analyze=not flatten)
    if flatten:
        plan = organizer.plan_flatten(lib)
    else:
        bad = organizer.validate_template((a.folders or "") + (a.rename or ""))
        if bad:
            sys.exit("Unknown tokens: " + ", ".join(bad))
        photos = export.select(lib, a.scope)
        plan = organizer.plan_organize(lib, photos, a.folders or "", a.rename or "",
                                       Path(a.dest) if a.dest else None, "copy" if a.copy else "move")
    for folder, n in plan.folders().items():
        print(f"  {n:5d}  {folder}")
    print(f"{len(plan.items)} photos ({plan.file_count} files) would be {'copied' if plan.mode == 'copy' else 'moved'}"
          f"; {plan.unchanged} already in place; {plan.collisions} name clashes.")
    if not a.yes:
        print("Preview only - add --yes to do it.")
        return
    journal, errors = organizer.apply_plan(lib, plan, _progress, remove_empty_folders=True,
                                           label="Flatten subfolders" if flatten else "Organise (command line)")
    lib.migrate_keys(journal)
    print(f"Done: {journal.done_count} files. {len(errors)} problems. Undo with:  python -m photo_sorter undo \"{lib.root}\"")


def cmd_xmp(a) -> None:
    from .core import export

    lib = _open(a.folder, a.recursive, analyze=False)
    n, errors = export.write_xmp_for(lib.photos, pick_label=a.pick_label)
    print(f"Wrote {n} sidecars, {len(errors)} errors.")


def cmd_undo(a) -> None:
    from .core import file_operations as fo

    root = Path(a.folder).resolve()
    for path in fo.list_journals(root):
        j = fo.Journal.load(path)
        if j.undoable:
            restored, errors = fo.undo_journal(j, root)
            print(f"Undid '{j.label or j.kind}': restored {restored} files, {len(errors)} problems.")
            for e in errors[:10]:
                print("  -", e)
            return
    print("Nothing to undo.")


def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        p = argparse.ArgumentParser(prog="python -m photo_sorter", description="Photography Tools")
        p.add_argument("first", nargs="?", help="folder to open (or a legacy tool name)")
        p.add_argument("folder", nargs="?")
        p.add_argument("--view", default=None, choices=["cull", "groups", "auto", "organize", "export", "history"])
        p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
        a = p.parse_args(argv)
        view, folder = a.view or "cull", None
        if a.first in LEGACY_VIEWS:
            view = a.view or LEGACY_VIEWS[a.first]
            folder = a.folder
        else:
            folder = a.first
        from .gui.app import main as gui_main

        gui_main(folder, view)
        return

    p = argparse.ArgumentParser(prog="python -m photo_sorter")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("folder")
        sp.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
        return sp

    sp = add("analyze", "analyse a folder and print the best photos")
    sp.add_argument("--top", type=int, default=10)
    sp.add_argument("--csv")
    sp.add_argument("--html")
    sp = add("cull", "suggest picks and rejects")
    sp.add_argument("--preset")
    sp.add_argument("--strategy", choices=["best_of_groups", "top_n", "top_percent", "threshold"])
    sp.add_argument("--count", type=int)
    sp.add_argument("--apply", action="store_true")
    sp = add("organize", "sort into folders / rename (preview unless --yes)")
    sp.add_argument("--folders", default="{YYYY}/{YYYY}-{MM}-{DD}")
    sp.add_argument("--rename", default="")
    sp.add_argument("--scope", default="all", choices=["all", "picks", "not_rejected", "rejects", "rated1", "rated3"])
    sp.add_argument("--dest")
    sp.add_argument("--copy", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp = add("flatten", "move photos from subfolders into the folder itself")
    sp.add_argument("--yes", action="store_true")
    sp = add("xmp", "write ratings/labels to XMP sidecars")
    sp.add_argument("--pick-label", default="Green")
    add("undo", "undo the most recent file operation")
    a = p.parse_args(argv)
    {"analyze": cmd_analyze, "cull": cmd_cull, "organize": cmd_organize,
     "flatten": lambda a: cmd_organize(a, flatten=True), "xmp": cmd_xmp, "undo": cmd_undo}[a.cmd](a)


if __name__ == "__main__":
    main()
