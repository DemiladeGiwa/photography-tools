"""Perceptual hashes (aHash / dHash / pHash) on top of OpenCV + NumPy.

Hashes are plain 64-bit Python ints so they can be cached as JSON and
compared with XOR + popcount.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np


def _bits_to_int(bits: np.ndarray) -> int:
    packed = np.packbits(bits.astype(np.uint8).flatten())
    return int.from_bytes(packed.tobytes(), "big")


def ahash(gray: np.ndarray, hash_size: int = 8) -> int:
    small = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    return _bits_to_int(small > small.mean())


def dhash(gray: np.ndarray, hash_size: int = 8) -> int:
    small = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    return _bits_to_int(small[:, 1:] > small[:, :-1])


def phash(gray: np.ndarray, hash_size: int = 8, highfreq_factor: int = 4) -> int:
    img_size = hash_size * highfreq_factor
    small = cv2.resize(gray, (img_size, img_size), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct_low = cv2.dct(small)[:hash_size, :hash_size]
    med = np.median(dct_low.flatten()[1:])  # ignore DC term (overall brightness)
    return _bits_to_int(dct_low > med)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1") if not hasattr(int, "bit_count") else (a ^ b).bit_count()


def file_digest(path: Path, chunk: int = 1 << 20) -> str:
    """Full-content hash, used only to confirm exact-duplicate candidates."""
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
