#!/usr/bin/env python3
"""Opens Photography Tools on the Cull screen.

Blur detection now lives there: tick "Only blurry" in the filters, and press H
in the loupe for the focus map. Kept so old shortcuts keep working.
Usage:  python BlurryPicMarker.py [folder]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photo_sorter.gui.app import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None, view="cull")
