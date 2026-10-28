"""Shared constants for the whole suite."""

from __future__ import annotations

RAW_EXTS = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf", ".rw2",
    ".dng", ".raf", ".pef", ".srw", ".3fr", ".iiq", ".rwl", ".x3f", ".erf", ".mef", ".mos",
}
STD_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".heic", ".heif"}
ALL_EXTS = RAW_EXTS | STD_EXTS

# Files that travel with a photo (edits / metadata written by other apps).
SIDECAR_EXTS = {".xmp", ".thm", ".pp3", ".dop", ".cos"}

# Hidden working folder created inside the opened library root.
CACHE_DIRNAME = ".photo_sorter_cache"
CACHE_DB_NAME = "signatures.sqlite3"
JOURNAL_DIRNAME = "journals"
STATE_FILE_NAME = "library_state.json"

# Folders that are never scanned when "include subfolders" is on.
# "_Rejected" is where rejects are moved to get them out of the way, so it is skipped too
# (open it directly to review it).
SKIP_DIR_NAMES = {CACHE_DIRNAME, "$RECYCLE.BIN", "System Volume Information", "__pycache__", ".venv", "_Rejected",
                  "_Deleted (exported)"}

# Bump whenever analysis output changes shape, so stale cache rows are recomputed.
ANALYSIS_VERSION = 5  # 5: JPEGs decoded with DCT scaling (faster; sharpness shifts by ~1 point)

# Longest edge (px) decoded for analysis. Sharpness is measured at this size
# (so missed focus is still visible); everything else uses FEATURE_MAX_DIM.
ANALYSIS_MAX_DIM = 1600
FEATURE_MAX_DIM = 1024
# Longest edge (px) of the thumbnails cached for the grid.
THUMB_MAX_DIM = 360
# Longest edge (px) of the preview image shown in the loupe at "fit" zoom.
PREVIEW_MAX_DIM = 2400

REJECTED_DIRNAME = "_Rejected"
# Fallback when the Recycle Bin is unavailable while deleting originals that were exported.
EXPORTED_TRASH_DIRNAME = "_Deleted (exported)"
DEFAULT_EXPORT_DIRNAME = "Selected"

COLOR_LABELS = ["Red", "Yellow", "Green", "Blue", "Purple"]
