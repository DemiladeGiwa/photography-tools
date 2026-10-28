"""Double-click to open Photography Tools (no console window)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photo_sorter.gui.app import main  # noqa: E402

main(sys.argv[1] if len(sys.argv) > 1 else None)
