"""Synthetic image generators used across the test suite.

Real photos aren't checked into the repo (deliberately — keep it light).
These generators produce controlled, reproducible stand-ins for the
categories the blur/grouping algorithms need to tell apart.
"""

from __future__ import annotations

import numpy as np
import cv2
import pytest


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def make_sharp_image(size: int = 400, seed: int = 0) -> np.ndarray:
    """High-frequency synthetic scene: random sharp-edged shapes on a
    textured background — plenty of real edges for Laplacian/Sobel to find."""
    rng = _rng(seed)
    img = np.full((size, size), 200, dtype=np.uint8)
    # background texture
    noise = rng.integers(-15, 15, (size, size), endpoint=True).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    for _ in range(25):
        x1, y1 = rng.integers(0, size, 2)
        x2, y2 = rng.integers(0, size, 2)
        color = int(rng.integers(0, 80))
        thickness = int(rng.integers(1, 4))
        cv2.line(img, (x1, y1), (x2, y2), color, thickness)
    for _ in range(10):
        cx, cy = rng.integers(size // 4, 3 * size // 4, 2)
        r = int(rng.integers(10, 40))
        cv2.circle(img, (cx, cy), r, int(rng.integers(0, 60)), 2)
    return img


def make_blurry_image(sharp: np.ndarray, ksize: int = 25) -> np.ndarray:
    return cv2.GaussianBlur(sharp, (ksize | 1, ksize | 1), 0)


def make_motion_blurred_image(sharp: np.ndarray, length: int = 25) -> np.ndarray:
    kernel = np.zeros((length, length), dtype=np.float32)
    kernel[length // 2, :] = 1.0 / length
    return cv2.filter2D(sharp, -1, kernel)


def make_noisy_image(sharp: np.ndarray, sigma: float = 35.0, seed: int = 1) -> np.ndarray:
    rng = _rng(seed)
    noise = rng.normal(0, sigma, sharp.shape)
    return np.clip(sharp.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def make_dark_image(sharp: np.ndarray, factor: float = 0.12) -> np.ndarray:
    return np.clip(sharp.astype(np.float64) * factor, 0, 255).astype(np.uint8)


def make_bright_image(sharp: np.ndarray, add: int = 180) -> np.ndarray:
    return np.clip(sharp.astype(np.int16) + add, 0, 255).astype(np.uint8)


def make_low_texture_image(size: int = 400, value: int = 180) -> np.ndarray:
    return np.full((size, size), value, dtype=np.uint8)


def make_sharp_subject_blurred_bg(size: int = 400, seed: int = 2) -> np.ndarray:
    sharp = make_sharp_image(size, seed)
    blurred_bg = make_blurry_image(sharp, ksize=31)
    out = blurred_bg.copy()
    c0, c1 = size // 3, 2 * size // 3
    out[c0:c1, c0:c1] = sharp[c0:c1, c0:c1]
    return out


@pytest.fixture
def sharp_gray():
    return make_sharp_image()


@pytest.fixture
def blurry_gray(sharp_gray):
    return make_blurry_image(sharp_gray)


# ---------------------------------------------------------------------------
# Folder-level fixtures: write real JPEG files with EXIF capture times.

from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402


def make_scene(seed: int, w: int = 900, h: int = 600) -> np.ndarray:
    """Colourful synthetic BGR scene with plenty of sharp edges."""
    rng = _rng(seed)
    base = np.zeros((h, w, 3), np.uint8)
    c1, c2 = rng.integers(30, 220, 3), rng.integers(30, 220, 3)
    for y in range(h):
        base[y] = (c1 * (1 - y / h) + c2 * (y / h)).astype(np.uint8)
    for _ in range(35):
        col = tuple(int(x) for x in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.circle(base, (x, y), int(rng.integers(8, 90)), col, -1)
        else:
            cv2.rectangle(base, (x, y), (x + int(rng.integers(10, 160)), y + int(rng.integers(10, 160))), col, -1)
    return base


def write_jpeg(path: Path, bgr: np.ndarray, when: datetime = None, model: str = "TestCam") -> Path:
    from PIL import Image

    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    ex = im.getexif()
    ex[0x0110] = model
    ex[0x010F] = "Test"
    if when is not None:
        ex.get_ifd(0x8769)[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
    im.save(path, exif=ex, quality=92)
    return path


@pytest.fixture
def shoot(tmp_path):
    """Three scenes: a 3-frame burst (one blurred), a 2-frame burst, and a single.

    Returns (folder, truth) where truth maps file name -> (scene, kind).
    """
    folder = tmp_path / "shoot"
    folder.mkdir()
    t = datetime(2026, 9, 20, 10, 0, 0)
    truth = {}
    n = 1
    for scene, kinds in ((0, ["sharp", "blur", "sharp"]), (1, ["sharp", "sharp"]), (2, ["sharp"])):
        img = make_scene(scene * 7 + 1)
        for k, kind in enumerate(kinds):
            M = np.float32([[1, 0, 3 * k], [0, 1, -2 * k]])
            f = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)
            if kind == "blur":
                f = cv2.GaussianBlur(f, (0, 0), 3)
            name = f"IMG_{n:04d}.JPG"
            write_jpeg(folder / name, f, t)
            truth[name] = (scene, kind)
            n += 1
            t += timedelta(seconds=1)
        t += timedelta(minutes=10)
    return folder, truth
