# Photography Tools (v4.3)

A fast desktop app for getting through a shoot. You can cull it, find bursts and duplicates, auto-pick the keepers, sort everything into folders and hand the result to Lightroom, all from one window.

Open a folder and it's analysed once in the background. Every screen then works from that same analysis and the same decisions. The results are cached, so reopening a folder is instant.

## For people you give it to

Send them **`PhotographyTools-4.3.0-Setup.exe`** (an installer with Start menu and desktop shortcuts, plus an optional "Open in Photography Tools" folder right-click) or the portable **`PhotographyTools-4.3.0-win64.zip`**. Unzip it anywhere and run `PhotographyTools.exe`. Python is not needed.

Windows may show "Windows protected your PC" the first time, because the app isn't code-signed. Click **More info › Run anyway**.

## Building the installer (you)

Double-click **`build.bat`**. It sets up `.venv`, installs everything, runs the tests and builds:

* `dist\PhotographyTools\` – the portable app
* `dist\PhotographyTools-<version>-win64.zip` – ready to share
* `dist\PhotographyTools-<version>-Setup.exe` – only if [Inno Setup 6](https://jrsoftware.org/isdl.php) (free) is installed

There's also a GitHub Actions workflow in `ci/build-windows.yml` (move it to `.github/workflows/` in your repo). Push to GitHub and every commit is built for you; tag `v4.3.0` and it publishes a Release with the zip and installer attached.

To release a new version, bump `__version__` in `photo_sorter/__init__.py` (and `pyproject.toml`), add a line to `CHANGELOG.md` and run `build.bat`.

## Updating an existing install (keep your place)

Your settings, recent folders, analysis caches and decisions are stored outside the program (`%USERPROFILE%\.photo_sorter` and a hidden `.photo_sorter_cache` in each photo folder), so an update never touches them.

* **Installed with Setup.exe:** run the new `PhotographyTools-<version>-Setup.exe`. It upgrades in place (same app ID) and keeps everything.
* **Portable folder / existing exe:** unzip `PhotographyTools-<version>-update.zip`, run `Update-PhotographyTools.bat` and give it the folder your current `PhotographyTools.exe` is in. Close the app first.

Select photos and press **Ctrl+J** (or right-click > *Save as JPG*) to write full-size JPEGs from RAW or any other format.

## Running from source

Double-click **`Photography Tools.bat`** (uses `.venv`) or **`Photography Tools.pyw`**, or:

```bash
python -m photo_sorter                      # reopens your last folder
python -m photo_sorter "D:\Shoots\Wedding"  # open a specific folder
```

Install / update dependencies with `pip install -r requirements.txt` (inside `.venv`).

## The screens

| Screen | What it's for |
| --- | --- |
| **Import** | Copies a memory card (found automatically) into your library, sorted into dated folders. RAW+JPEG pairs stay together, and anything already imported is skipped, so re-inserting a card is safe. It checks free space first and verifies every copy. |
| **Cull** | A thumbnail grid that stays fast with thousands of photos. Filter by flag, stars, colour label, blur or groups, or search. The inspector on the right shows the scores, *why* a photo scored that way, a histogram, EXIF and its group. Space opens the loupe (zoom, focus map, clipping warning); C compares 2-4 photos side by side with synced zoom. |
| **Groups** | Bursts, similar shots and duplicates, each with its best frame marked. Click a group's header to select all of it. "Keep best of every group" picks the best frame and rejects the rest in one go. |
| **Auto-Cull** | Choose a genre preset, a strategy (best of each group, top N, top %, or above a score) and what to reject. You see the result count live before applying. Nothing moves: it only sets flags you can review. |
| **Insights** | Keeper rate, picks, blur count and average quality, with animated charts of when you shot, quality and sharpness spread, cameras, lenses, focal lengths, apertures, ISO and shutter speeds. Each chart shows what you kept against what you shot. |
| **Organise** | Sort into folders and rename from templates, e.g. `{YYYY}/{YYYY}-{MM}-{DD}`, `{date} Event {event}`, `{camera}`, `{flag}`, `{rating}`, `{group}`, with renames like `{YYYY}{MM}{DD}_{seq:4}`. Every change is previewed first. **Flatten subfolders** moves everything back up into one folder (this replaces the old PowerShell script). |
| **Export** | Copy or move picks (with their RAW/JPEG partners and sidecars) and write XMP ratings for Lightroom / Capture One / Bridge. Move rejects to `_Rejected` or the Recycle Bin. Make an HTML contact sheet, a CSV, a file list, or copy the names for Lightroom's filter. |
| **History** | Every file operation is journaled, and you can undo any of them here. |

## Keyboard

`P` pick · `X`/`Delete` reject · `U` clear · `1`–`5` stars (`0` clears) · `6 7 8 9` red/yellow/green/blue label · arrows move · `Space`/`Enter` loupe · `Z` 100% · `H` focus map · `K` clipping · `F` filmstrip · `C` compare · `Tab` next photo in compare · `Esc` back · `Ctrl+Z` undo · `Ctrl+A` select all · `Ctrl+O` open folder · `F5` analyse · `?` all shortcuts.

**Auto-advance** (top bar) moves to the next photo after each pick, reject or rating. The loupe has a filmstrip of neighbouring photos (click or scroll it; `F` hides it). Click the counts in the top bar to jump straight to your picks, rejects or what's left to review. Right-click any photo and choose **Find similar photos** to see every shot like it across the whole folder. `Ctrl+F` searches, `Ctrl+I` opens Import, `F11` goes full screen and `Ctrl+,` opens Settings (threads, auto-analyse, animations).

## How the analysis works

* **Sharpness** measures how *steep* edges are relative to their own contrast. That makes it independent of subject matter, brightness and noise; the old texture-based score swung 100x between scenes. It is measured on a 1600 px version, so missed focus shows up. Each frame is scored in a 5×5 grid (the focus map) and per edge direction, so motion blur is told apart from missed focus. Focus profiles judge it differently: Portrait looks at the subject, Landscape wants edge-to-edge sharpness, and Action penalises motion blur.
* **Exposure** looks at clipped highlights (broad areas vs. small specular glints), crushed shadows, midtone balance and contrast. It recognises intentional high-key, low-key and ETTR shots.
* **Grouping** blends perceptual hashes (with an exposure-robust variant, so brackets still group), a structural comparison and colour, with a burst-timing bonus. Clusters only merge if they are similar *on average*, which stops chains of near-matches from gluing unrelated scenes together. Duplicates are found across the whole shoot.
* **Best of group:** frames in a group share content, so their raw sharpness is directly comparable and counts heavily.

## Safety

* RAW+JPEG files with the same name are treated as **one photo**: one decision, and they always move together with their `.xmp` sidecars.
* Nothing is ever overwritten. Name clashes get ` (1)`, ` (2)` and so on.
* "Delete" means the Recycle Bin. If that isn't available, files go to `_Rejected` instead of being permanently deleted.
* XMP sidecars are **merged**: your Lightroom edits in an existing sidecar are kept, and only the rating and label change. Rejects get Lightroom's "rejected" rating (−1). Sidecars use Lightroom's naming (`IMG_0001.xmp`).
* Decisions autosave to `.photo_sorter_cache/` inside the photo folder. They follow files when they're renamed or organised. Existing XMP ratings are imported the first time you open a folder.

## Command line

```bash
python -m photo_sorter analyze FOLDER --csv report.csv --html sheet.html
python -m photo_sorter cull FOLDER --preset "Sports & Action" --apply
python -m photo_sorter organize FOLDER --folders "{YYYY}/{date}" --rename "{date}_{seq:4}"   # preview
python -m photo_sorter organize FOLDER --folders "{flag}" --yes                               # do it
python -m photo_sorter flatten FOLDER --yes
python -m photo_sorter xmp FOLDER
python -m photo_sorter undo FOLDER
```

Add `-r` to include subfolders.

## Tests

```bash
python -m pytest
```

## Project layout

```
photo_sorter/
  core/        engine - no UI code, fully tested
    library.py        photos, RAW+JPEG pairing, decisions, scoring, groups
    analysis.py       one decode per photo -> every signal + thumbnail, cached
    blur_detector.py  edge-steepness sharpness, focus map, motion detection
    exposure.py       exposure scoring        scoring.py   combined quality
    grouping.py       bursts / similar / duplicates      selection.py  auto-cull
    organizer.py      folder + rename templates, flatten
    export.py         copy/move, XMP, rejects, CSV        html_report.py  contact sheet
    file_operations.py journaled moves, undo, Recycle Bin, XMP merge
    cache.py          SQLite cache        metadata.py   EXIF      image_loader.py  decoding
    importer.py       card import         web_export.py share-ready JPEGs   stats.py  insights
  gui/         the app: app.py (window), cull_view.py, thumbgrid.py (virtual grid),
               viewer.py (loupe/compare), inspector.py, groups_view / auto_view / organize_view / export_view / history_view (other screens), keys.py (shortcuts), images.py,
               welcome.py, import_view.py, insights_view.py, dialogs.py, theme.py (style + animation)
installer/   Inno Setup script         scripts/  build_standalone.py, make_icon.py
build.bat    one-click Windows build   ci/  GitHub Actions build (copy to .github/workflows/)
  tests/
```

## Building a standalone .exe

```bash
python scripts/build_standalone.py            # dist/PhotographyTools/
python scripts/build_standalone.py --onefile  # dist/PhotographyTools.exe
```
