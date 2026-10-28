from __future__ import annotations

import io
import types

import cv2
import numpy as np
from PIL import Image

from photo_sorter.core import image_loader as il


def _jpeg_bytes(w: int, h: int, exif_orientation: int = 0) -> bytes:
    img = Image.fromarray(np.random.default_rng(0).integers(0, 255, (h, w, 3), dtype=np.uint8))
    buf = io.BytesIO()
    kw = {"quality": 85}
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        kw["exif"] = ex.tobytes()
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def test_jpeg_size_reads_header_past_exif():
    assert il.jpeg_size(_jpeg_bytes(640, 480)) == (640, 480)
    assert il.jpeg_size(_jpeg_bytes(300, 200, exif_orientation=6)) == (300, 200)  # stored size, before rotation
    assert il.jpeg_size(np.frombuffer(_jpeg_bytes(64, 32), np.uint8)) == (64, 32)
    assert il.jpeg_size(b"not a jpeg") is None
    assert il.jpeg_size(b"") is None


def test_reduced_flag_keeps_enough_pixels():
    assert il.reduced_flag((6000, 4000), 360) == cv2.IMREAD_REDUCED_COLOR_8
    assert il.reduced_flag((6000, 4000), 1600) == cv2.IMREAD_REDUCED_COLOR_2
    assert il.reduced_flag((6000, 4000), 1400) == cv2.IMREAD_REDUCED_COLOR_4
    assert il.reduced_flag((2000, 1500), 1600) == cv2.IMREAD_COLOR
    assert il.reduced_flag((6000, 4000), None) == cv2.IMREAD_COLOR
    assert il.reduced_flag(None, 360) == cv2.IMREAD_COLOR
    base = cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
    assert il.reduced_flag((6000, 4000), 360, base) == cv2.IMREAD_REDUCED_COLOR_8 | cv2.IMREAD_IGNORE_ORIENTATION


def test_reduced_decode_size_and_orientation(tmp_path):
    p = tmp_path / "big.jpg"
    p.write_bytes(_jpeg_bytes(3200, 2000))
    img, err = il.load_bgr(p, 700)
    assert err is None and max(img.shape[:2]) == 700 and img.shape[1] > img.shape[0]
    full, _ = il.load_bgr(p, None)
    assert full.shape[:2] == (2000, 3200)
    r = tmp_path / "rotated.jpg"
    r.write_bytes(_jpeg_bytes(3200, 2000, exif_orientation=6))  # portrait shot stored landscape
    img, err = il.load_bgr(r, 700)
    assert err is None and img.shape[0] == 700 and img.shape[0] > img.shape[1]


class _FakeRaw:
    def __init__(self, preview: bytes, sensor=(6000, 4000)):
        self._preview = preview
        self.sizes = types.SimpleNamespace(flip=0, width=sensor[0], height=sensor[1])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_thumb(self):
        return types.SimpleNamespace(format="jpeg", data=self._preview)

    def postprocess(self, **kw):
        w, h = self.sizes.width, self.sizes.height
        if kw.get("half_size"):
            w, h = w // 2, h // 2
        return np.full((h, w, 3), 7, np.uint8)  # recognisably not the preview


def _fake_rawpy(monkeypatch, preview: bytes):
    fake = types.SimpleNamespace(imread=lambda path: _FakeRaw(preview),
                                 ThumbFormat=types.SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"))
    monkeypatch.setattr(il, "rawpy", fake)
    monkeypatch.setattr(il, "HAS_RAWPY", True)


def test_raw_small_preview_is_fine_for_screen_but_not_for_full_size(tmp_path, monkeypatch):
    _fake_rawpy(monkeypatch, _jpeg_bytes(1616, 1080))  # e.g. older Sony ARW
    raw = tmp_path / "DSC0001.ARW"
    raw.write_bytes(b"x")
    img, err = il.load_bgr(raw, 1000)
    assert err is None and img.shape[:2] == (668, 1000)  # embedded preview, resized
    full, err = il.load_bgr(raw, None)
    assert err is None and full.shape[:2] == (4000, 6000) and full.mean() == 7  # demosaiced, not the 1616 px preview


def test_raw_full_size_preview_is_used(tmp_path, monkeypatch):
    preview = _jpeg_bytes(6000, 4000)
    _fake_rawpy(monkeypatch, preview)
    raw = tmp_path / "IMG_0001.CR3"
    raw.write_bytes(b"x")
    img, err = il.load_bgr(raw, 360)
    assert err is None and max(img.shape[:2]) == 360
    full, err = il.load_bgr(raw, None)
    assert err is None and full.shape[:2] == (4000, 6000) and full.mean() != 7  # the embedded full-size JPEG
