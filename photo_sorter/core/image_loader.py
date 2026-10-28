"""
Unified image decoding for RAW and standard raster formats.

* Analysis and thumbnails only need small images, so every loader takes a
  ``max_dim`` and resizes immediately.
* RAW files first try the camera's embedded JPEG preview (milliseconds),
  falling back to a half-size demosaic. The RAW orientation flag is applied
  to embedded previews so portrait shots are not shown sideways.
* Standard files decode with OpenCV (respects EXIF orientation) and fall
  back to Pillow for formats/variants OpenCV cannot read.
* Every public function returns ``(image, error)`` and never raises, so one
  corrupt file can't kill a batch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .constants import RAW_EXTS

try:
    import rawpy  # type: ignore

    HAS_RAWPY = True
except ImportError:  # pragma: no cover - depends on environment
    rawpy = None
    HAS_RAWPY = False

try:
    from PIL import Image, ImageOps

    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False

try:  # optional HEIC support
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except Exception:  # pragma: no cover
    HAS_HEIF = False


class ImageLoadError(Exception):
    pass


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTS


def resize_to_max_dim(img: np.ndarray, max_dim: Optional[int]) -> np.ndarray:
    if not max_dim:
        return img
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return img
    scale = max_dim / float(longest)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_REDUCED_FLAGS = ((8, cv2.IMREAD_REDUCED_COLOR_8), (4, cv2.IMREAD_REDUCED_COLOR_4), (2, cv2.IMREAD_REDUCED_COLOR_2))


def jpeg_size(data) -> Optional[Tuple[int, int]]:
    """(width, height) from a JPEG's frame header without decoding it, or None."""
    buf = bytes(data[:min(len(data), 1 << 19)])
    n = len(buf)
    if n < 4 or buf[0] != 0xFF or buf[1] != 0xD8:
        return None
    i = 2
    while i + 9 < n:
        if buf[i] != 0xFF:
            i += 1
            continue
        marker = buf[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # markers without a length
            i += 2
            continue
        seg_len = (buf[i + 2] << 8) | buf[i + 3]
        if marker in _SOF_MARKERS:
            h = (buf[i + 5] << 8) | buf[i + 6]
            w = (buf[i + 7] << 8) | buf[i + 8]
            return (w, h) if w and h else None
        if marker in (0xD9, 0xDA) or seg_len < 2:  # end of image / start of scan before a frame header
            return None
        i += 2 + seg_len
    return None


def reduced_flag(size: Optional[Tuple[int, int]], max_dim: Optional[int], base: int = cv2.IMREAD_COLOR) -> int:
    """Biggest libjpeg DCT down-scale (1/8, 1/4, 1/2) that still leaves at least ``max_dim`` px.

    Decoding at reduced scale skips most of the work, so a 24 MP JPEG decodes 3-6x faster.
    """
    if not max_dim or not size:
        return base
    longest = max(size)
    for factor, flag in _REDUCED_FLAGS:
        if longest // factor >= max_dim:
            return flag | (base & cv2.IMREAD_IGNORE_ORIENTATION)
    return base


def _apply_raw_flip(img: np.ndarray, flip: int) -> np.ndarray:
    if flip == 3:
        return cv2.rotate(img, cv2.ROTATE_180)
    if flip == 5:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if flip == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def _decode_raw_bgr(path: Path, max_dim: Optional[int], allow_preview: bool = True) -> np.ndarray:
    if not HAS_RAWPY:
        raise ImageLoadError("RAW support needs the 'rawpy' package (pip install rawpy)")
    try:
        with rawpy.imread(str(path)) as raw:
            flip = int(getattr(raw.sizes, "flip", 0) or 0)
            if allow_preview:
                try:
                    if max_dim:
                        need = min(max_dim, 1000)
                    else:
                        # Full size was asked for: only an embedded preview at (nearly) sensor size will do.
                        # Some cameras (older Sony, Olympus...) embed a ~1600 px preview.
                        need = int(0.9 * max(int(raw.sizes.width or 0), int(raw.sizes.height or 0))) or 1000
                    thumb = raw.extract_thumb()
                    img = None
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        buf = np.frombuffer(thumb.data, dtype=np.uint8)
                        base = cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
                        img = cv2.imdecode(buf, reduced_flag(jpeg_size(buf), max_dim, base))
                    elif thumb.format == rawpy.ThumbFormat.BITMAP:
                        img = cv2.cvtColor(np.asarray(thumb.data), cv2.COLOR_RGB2BGR)
                    if img is not None and max(img.shape[:2]) >= need:
                        return resize_to_max_dim(_apply_raw_flip(img, flip), max_dim)
                except Exception:
                    pass
            half = bool(max_dim) and max_dim <= 2600
            rgb = raw.postprocess(use_camera_wb=True, half_size=half, output_bps=8)
            return resize_to_max_dim(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), max_dim)
    except ImageLoadError:
        raise
    except Exception as e:
        raise ImageLoadError(f"RAW decode failed: {e}") from e


def _decode_pil_bgr(path: Path, max_dim: Optional[int]) -> np.ndarray:
    if not HAS_PIL:
        raise ImageLoadError("unsupported or corrupt image data")
    try:
        with Image.open(str(path)) as im:
            if max_dim:
                im.draft("RGB", (max_dim, max_dim))
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            arr = np.asarray(im)
    except Exception as e:
        raise ImageLoadError(f"unsupported or corrupt image data ({e})") from e
    return resize_to_max_dim(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), max_dim)


def _decode_std_bgr(path: Path, max_dim: Optional[int]) -> np.ndarray:
    # np.fromfile + imdecode handles unicode paths on Windows (cv2.imread doesn't).
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError as e:
        raise ImageLoadError(f"unreadable file: {e}") from e
    if data.size == 0:
        raise ImageLoadError("empty file")
    flags = cv2.IMREAD_COLOR
    # Reduced (DCT-scaled) decoding is much faster for big JPEGs; the frame header says how far we can go.
    if max_dim and path.suffix.lower() in (".jpg", ".jpeg"):
        reduced = reduced_flag(jpeg_size(data), max_dim)
        if reduced != flags:
            img = cv2.imdecode(data, reduced)
            if img is not None and max(img.shape[:2]) >= max_dim:
                return resize_to_max_dim(img, max_dim)
    img = cv2.imdecode(data, flags)
    if img is None:
        return _decode_pil_bgr(path, max_dim)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return resize_to_max_dim(img, max_dim)


def load_bgr(path: Path, max_dim: Optional[int]) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Load any supported image as BGR uint8, downscaled to ``max_dim`` (None = full size)."""
    try:
        if is_raw(path):
            return _decode_raw_bgr(path, max_dim), None
        return _decode_std_bgr(path, max_dim), None
    except ImageLoadError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def load_gray(path: Path, max_dim: Optional[int]) -> Tuple[Optional[np.ndarray], Optional[str]]:
    bgr, err = load_bgr(path, max_dim)
    if bgr is None:
        return None, err
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), None


def encode_jpeg(bgr: np.ndarray, quality: int = 85) -> Optional[bytes]:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


def make_thumbnail_jpeg_bytes(path: Path, max_dim: int) -> Tuple[Optional[bytes], Optional[str]]:
    bgr, err = load_bgr(path, max_dim)
    if bgr is None:
        return None, err
    data = encode_jpeg(bgr)
    return (data, None) if data else (None, "thumbnail encode failed")


def bgr_to_pil(bgr: np.ndarray):
    """Convert a BGR array to a PIL RGB image (for Tk display / HTML export)."""
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
