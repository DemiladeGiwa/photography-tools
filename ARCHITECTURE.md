# Photography Tools: System Architecture (v4.3.0)

A Windows-first desktop app (Python 3.12 + Tkinter) for culling, grouping, organising and exporting photo shoots. One window, one analysis pass per photo, one shared model of decisions. This file is the map for anyone (human or Claude) improving it. Read `CLAUDE.md` first for the rules.

## 1. Product in one paragraph

The user opens a folder. Every photo is analysed once in the background (sharpness, exposure, perceptual signatures, thumbnail) and cached. All screens then read the same `Library` object: Cull (grid, loupe, compare), Groups (bursts/duplicates with a best frame), Auto-Cull (preset-driven flag setting), Insights (charts), Organise (folder/rename templates), Export (copy/move picks, XMP, share-ready JPEGs, Save as JPG, contact sheet, delete exported originals), Import (memory card), History (undo any file operation).

## 2. Layers

```
Entry points   Photography Tools.pyw / .bat, python -m photo_sorter (CLI + GUI), legacy launchers
   │
GUI            photo_sorter/gui/   Tkinter. No analysis logic. Talks to core only through Library + core modules.
   │
Core           photo_sorter/core/  Pure Python engine. No tkinter imports. Fully unit-tested.
   │
Storage        per-folder .photo_sorter_cache/  +  ~/.photo_sorter/   (never inside the exe)
```

Hard rule: **core must never import gui.** GUI calls long work through `App.run_task`.

## 3. Core modules (`photo_sorter/core/`)

| Module | Responsibility |
| --- | --- |
| `constants.py` | Extensions (RAW/STD/sidecar), cache names, `ANALYSIS_VERSION`, decode sizes, skip-folder names, colour labels |
| `library.py` | **Central model.** `Library` (scan, analyse, score, group, decisions, state save/restore, `find_similar`, `migrate_keys`), `Photo` (one logical photo = display file + optional RAW partner), `PhotoFilter`, `ScoringSettings` |
| `analysis.py` | Single decode per photo → every signal + thumbnail. `analyze_many` runs a thread pool (`DEFAULT_WORKERS`/`MAX_WORKERS`) |
| `blur_detector.py` | Edge-steepness sharpness (relative to local contrast, noise-aware `contrast_floor`), 5×5 focus map, per-direction motion-blur detection |
| `exposure.py` | Clipping, crushed shadows, midtones, contrast; recognises high-key/low-key/ETTR |
| `scoring.py` | Combines sharpness + exposure into quality; focus profiles (Portrait/Landscape/Action) |
| `hashing.py`, `similarity.py`, `grouping.py` | Perceptual hashes (incl. exposure-robust), structural + colour similarity, average-linkage clustering, burst-time bonus, best-of-group |
| `selection.py` | Auto-cull strategies (best of group, top N, top %, above score) |
| `presets.py` | Genre presets, user presets in `~/.photo_sorter/presets.json` |
| `organizer.py` | Folder/rename templates (`{YYYY}`, `{date}`, `{camera}`, `{flag}`, `{seq:4}`…), plan/apply, flatten |
| `export.py` | `ExportOptions`, copy/move picks with RAW partners + sidecars, XMP writing, reject handling, `find_exported`, `delete_originals`, CSV/list |
| `web_export.py` | `WebExportOptions`, resize/sharpen/watermark/EXIF, `export_web`; **`save_as_jpg`** = full-size, q95 wrapper |
| `importer.py` | Memory-card import with dedupe, free-space check, copy verification |
| `file_operations.py` | `Transaction` + `Journal` (every move/copy is journaled and undoable), `undo_journal`, `send_to_trash`, XMP read/merge-write, sidecar handling |
| `cache.py` | SQLite `SignatureCache` keyed `name|size|mtime` |
| `metadata.py`, `image_loader.py` | EXIF (exifread/Pillow), RAW/HEIC/standard decoding (`load_bgr`, `bgr_to_pil`, `encode_jpeg`) |
| `stats.py`, `html_report.py` | Insights numbers; HTML contact sheet |

## 4. GUI modules (`photo_sorter/gui/`)

| Module | Responsibility |
| --- | --- |
| `app.py` | `App` (root window): nav, `views` dict {welcome, import, cull, groups, auto, insights, organize, export, history}, `switch_view` (falls back to welcome), clickable top-bar counts (`show_flag_filter`), decisions (`decide_flag/rating/label`, undo stack), `run_task(label, work(progress, stop), on_done, on_error)`, toasts, settings, `save_selected_as_jpg`, `_targets()` (current view's selection) |
| `cull_view.py` | Filters, grid/loupe/compare modes, context menu, `show_similar` |
| `keys.py` | `SHORTCUTS` (the `?` list) and `handle_key` (global key handler) |
| `thumbgrid.py` | Virtual-canvas grid that stays fast with thousands of photos; selection model. Every cell's items are tagged `k<index>` so hover/selection/thumbnail/decision changes redraw single cells (`redraw_keys`); `redraw()` is the full redraw |
| `viewer.py` | `Loupe` (+ `Filmstrip`), `CompareView` (synced zoom), focus map, clipping overlay, cross-fade. Fit frames are cached; the grid thumbnail stands in while the preview decodes |
| `inspector.py` | Right panel: scores, explanation, histogram, EXIF, group. Built once, updated in place, and its height kept stable (any height change costs ~50 ms of Tk relayout on Windows) |
| `groups_view.py`, `auto_view.py`, `organize_view.py`, `export_view.py`, `history_view.py` | One module per secondary screen; `common.header` is shared |
| `welcome.py`, `import_view.py`, `insights_view.py`, `dialogs.py` | Self-explanatory |
| `theme.py` | Dark + amber style, `T.animate`, `T.count_up`, `T.Toast`, `T.ScrollFrame`, `T.Card`, `T.bind_wrap`, `px()` DPI scaling, settings load/save |
| `images.py` | `ImageService`: thumbnail/preview loading off the UI thread. Thumbnails use a `LifoPool` (newest request first, oldest dropped past 400) |
| `assets/` | `icon.png`, `icon.ico` |

## 5. Key data flows

**Open folder:** `App.open_folder` → `Library.scan` (pair RAW+JPEG by stem, import existing XMP ratings first time, restore saved decisions) → `Library.analyze` in a background task (cache hit when `version == ANALYSIS_VERSION`) → `_score_one` → `compute_groups` (restored from `library_state.json` if the groups signature matches) → views refresh.

**Decision:** keypress → `App.decide_*` → `push_undo` snapshot → `Library.set_*` → `mark_dirty` → autosave → stamp animation/toast.

**File operation (organise, export move, rejects, JPG export):** build plan → `Transaction(lib.root, kind, label)` journals each op (`copy`/`move`) → `Library.migrate_keys(journal)` when files move so decisions follow → History view can `undo_journal`.

**Save as JPG:** `App.save_selected_as_jpg` (Ctrl+J, context menu, Export button) → folder dialog → `run_task` → `web_export.save_as_jpg` → `export_web` with `long_edge=0, quality=95, sharpen=False, keep_exif=True` → output `<dest>/<folder name> - JPG/`; journaled as "copy" so undo deletes the outputs.

## 6. Persistence (what survives an update)

| Location | Contents |
| --- | --- |
| `<photo folder>/.photo_sorter_cache/signatures.sqlite3` | Analysis cache |
| `<photo folder>/.photo_sorter_cache/library_state.json` | Flags, stars, labels, groups |
| `<photo folder>/.photo_sorter_cache/journals/` | Undo journals |
| `~/.photo_sorter/settings.json` | recent, geometry, zoomed, sort, thumb_size, auto_advance, recursive, reopen_last, auto_analyze, animations, threads, watermark, import_dest, import_template, jpg_dest, filmstrip |
| `~/.photo_sorter/presets.json`, `error.log` | User presets, crash log |

**Compatibility contract:** the user updates in place and expects to "continue where they left off". Never rename or reshape these files without a migration. Adding new settings keys with defaults is safe. Changing analysis output requires bumping `ANALYSIS_VERSION` (cache is recomputed, decisions are kept).

## 7. Concurrency model

Tk runs on the main thread only. `run_task` starts a worker thread and marshals progress/results back with a queue polled by `after()`. Analysis uses a thread pool inside the worker (OpenCV/NumPy release the GIL). Workers receive a `stop()` callable and must check it. Never touch widgets from a worker.

## 8. Build and release

* `build.bat` → creates `.venv`, installs `requirements.txt`, runs pytest, runs `scripts/build_standalone.py`.
* `scripts/build_standalone.py` → PyInstaller onedir to `dist/PhotographyTools/`, `PhotographyTools-<ver>-win64.zip`, `PhotographyTools-<ver>-update.zip` (contains `Update-PhotographyTools.bat`, which robocopies over an existing folder), and `…-Setup.exe` if Inno Setup 6 is installed.
* `installer/PhotographyTools.iss` → **`AppId` `{6C3E2B1A-4F5D-4C8E-9A61-2E7F0B9D4A10}` must never change**, so new installers upgrade the old install in place.
* Version lives in `photo_sorter/__init__.py` and `pyproject.toml` (and the `.iss` default). Bump all, add a CHANGELOG entry and README mentions.
* `ci/build-windows.yml` is the GitHub Actions workflow (copy to `.github/workflows/`).
* App is unsigned (SmartScreen warns on first run).

## 8b. Tests

`photo_sorter/tests/` (77 tests, run `python -m pytest`; on Linux `xvfb-run -a python -m pytest -q`). `conftest.py` provides `make_scene`, `write_jpeg`, and the `shoot` fixture (3-frame burst with one blur, 2-frame burst, a single; returns `(folder, truth)`). GUI smoke tests are in `test_cli_and_gui.py`.

## 9. Known weak spots and improvement backlog

Ordered roughly by value:

1. **No Windows CI run yet.** The exe/installer has only been built by the user locally; the frozen build was verified on Linux. Wire up and verify `ci/build-windows.yml`.
2. **Not code-signed.** SmartScreen warning for every new user.
3. **`gui/app.py` (~865 lines) is still big.** `views.py` was split in 4.3 and keys moved to `keys.py`; the task runner and decision methods could move next.
4. **Save as JPG has no options dialog** (fixed q95, full size, EXIF kept). Add quality/size/ rename-template choices and remember them.
5. **Auto-update is manual.** Could add a "check for updates" against GitHub Releases.
6. **Group quality** is good on bursts but untested on large, varied real shoots; consider a labelled regression set and threshold tuning.
7. **Video, HEIC edge cases, very large (>20k) folders** are lightly tested.
8. **Settings dialog** lacks export defaults, thumbnail cache location, and theme options.
9. **Accessibility:** contrast and keyboard navigation of non-grid screens, High-DPI checks on Windows.
10. **Legacy launchers** (`PhotographyHub.py`, `RawSelector.py`, `PhotoGrouper.py`, `BlurryPicMarker.py`) are thin shims kept for old habits; remove when the user agrees.

## 10. Pitfalls already hit (don't repeat)

* Binding a label's `wraplength` to its own `<Configure>` froze the UI (infinite loop). Bind to the container with `T.bind_wrap`.
* Noise inflated sharpness; the `contrast_floor = min(60, max(18, 6*noise))` guard fixed it.
* RAW+JPEG pairs duplicated sidecars (`A (1).xmp`); `Transaction._fresh_sidecars` tracks handled sidecars.
* `render_folder` must drop `.` and `..` path parts.
* Organising into subfolders made photos vanish from a non-recursive view; reload with `recursive=True`.
* Files committed to the user's PC once silently did not land: always verify with md5.
* Protected path: `.github/workflows` can't be written from the sandbox.
