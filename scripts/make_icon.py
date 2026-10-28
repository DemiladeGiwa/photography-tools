"""Draws the app icon (photo_sorter/gui/assets/icon.png + icon.ico). Run once; output is committed."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "photo_sorter" / "gui" / "assets"
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=(24, 25, 30, 255))
cx = cy = S / 2
R = 330
amber = (226, 160, 63, 255)
d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=amber, width=58)
# six aperture blades
for k in range(6):
    a = math.radians(k * 60 - 15)
    r1, r2 = 95, 300
    p1 = (cx + r1 * math.cos(a), cy + r1 * math.sin(a))
    p2 = (cx + r2 * math.cos(a + 0.95), cy + r2 * math.sin(a + 0.95))
    d.line([p1, p2], fill=amber, width=46)
    d.ellipse((p1[0] - 23, p1[1] - 23, p1[0] + 23, p1[1] + 23), fill=amber)
    d.ellipse((p2[0] - 23, p2[1] - 23, p2[0] + 23, p2[1] + 23), fill=amber)
# small "pick" dot
d.ellipse((S - 300, S - 300, S - 150, S - 150), fill=(63, 185, 107, 255))
OUT.mkdir(parents=True, exist_ok=True)
img.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("written to", OUT)
