#!/usr/bin/env python3
"""Opens Photography Tools on the Groups screen (the old Photo Grouper).

Kept so old shortcuts keep working. Usage:  python PhotoGrouper.py [folder]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photo_sorter.gui.app import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None, view="groups")
