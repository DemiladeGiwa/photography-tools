"""
Metadata extraction (EXIF-family) for JPEG/TIFF/HEIC and, where possible,
RAW files.

Only ever opened once per file: ``extract_metadata`` is the single entry
point every caller should use, and its result is what gets cached by
:mod:`cache`. Nothing else in this project should call ``Image.open`` or
``exifread`` directly for metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .constants import RAW_EXTS

try:
    from PIL import Image, ExifTags

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import exifread  # type: ignore — pure-python, reads RAW/TIFF-container EXIF too

    HAS_EXIFREAD = True
except ImportError:
    HAS_EXIFREAD = False


@dataclass
class PhotoMetadata:
    datetime_original: Optional[str] = None  # ISO 8601 string (JSON-friendly)
    has_subsecond: bool = False
    make: Optional[str] = None
    model: Optional[str] = None
    lens: Optional[str] = None
    orientation: int = 1
    width: Optional[int] = None
    height: Optional[int] = None
    iso: Optional[int] = None
    f_number: Optional[float] = None
    exposure_time: Optional[str] = None
    focal_length: Optional[float] = None

    def dt(self) -> Optional[datetime]:
        if not self.datetime_original:
            return None
        try:
            # Naive local capture time: mixing aware/naive datetimes breaks sorting.
            return datetime.fromisoformat(self.datetime_original).replace(tzinfo=None)
        except ValueError:
            return None

    def format_exif_summary(self) -> str:
        """Format a clean photographer's EXIF summary string."""
        parts = []
        if self.model:
            parts.append(self.model)
        if self.focal_length:
            parts.append(f"{self.focal_length:.0f}mm")
        if self.f_number:
            parts.append(f"f/{self.f_number:.1f}")
        if self.exposure_time:
            parts.append(f"{self.exposure_time}s" if not self.exposure_time.endswith("s") else self.exposure_time)
        if self.iso:
            parts.append(f"ISO {self.iso}")
        return "  \u2022  ".join(parts) if parts else "No EXIF data"

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "PhotoMetadata":
        # Filter out unknown keys for backwards compatibility
        valid_keys = PhotoMetadata.__annotations__.keys()
        clean = {k: v for k, v in d.items() if k in valid_keys}
        return PhotoMetadata(**clean)


_EXIF_DT_FMT = "%Y:%m:%d %H:%M:%S"


def _parse_exif_dt(raw: str, subsec: Optional[str], offset: Optional[str]) -> Optional[datetime]:
    try:
        dt = datetime.strptime(raw.strip(), _EXIF_DT_FMT)
    except (ValueError, AttributeError):
        return None
    if subsec:
        try:
            frac = float("0." + subsec.strip())
            dt += timedelta(seconds=frac)
        except ValueError:
            pass
    if offset:
        try:
            sign = 1 if offset[0] == "+" else -1
            hh, mm = offset[1:].split(":")
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
        except (ValueError, IndexError):
            pass
    return dt


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            return float(val.numerator) / float(val.denominator) if val.denominator else None
        if hasattr(val, "values") and len(val.values) > 0:
            v = val.values[0]
            if hasattr(v, "num") and hasattr(v, "den"):
                return float(v.num) / float(v.den) if v.den else None
        return float(val)
    except Exception:
        return None


def _safe_exposure_str(val) -> Optional[str]:
    if val is None:
        return None
    try:
        f = _safe_float(val)
        if f is None or f <= 0:
            return str(val)
        if f < 0.5:
            denom = int(round(1.0 / f))
            return f"1/{denom}"
        return f"{f:.1f}"
    except Exception:
        return str(val)


def _from_pil(path: Path) -> PhotoMetadata:
    md = PhotoMetadata()
    with Image.open(str(path)) as img:
        md.width, md.height = img.size
        exif = img.getexif()
        if not exif:
            return md
        md.orientation = int(exif.get(0x0112, 1))
        make, model = exif.get(0x010F), exif.get(0x0110)
        md.make = str(make).strip("\x00 ").strip() if make else None
        md.model = str(model).strip("\x00 ").strip() if model else None

        exif_ifd = {}
        try:
            exif_ifd = exif.get_ifd(0x8769)  # Exif IFD pointer
        except Exception:
            pass

        raw_dt = exif_ifd.get(0x9003) or exif.get(0x0132)  # DateTimeOriginal / DateTime
        subsec = exif_ifd.get(0x9291)  # SubSecTimeOriginal
        offset = exif_ifd.get(0x9011)  # OffsetTimeOriginal
        lens = exif_ifd.get(0xA434)  # LensModel
        if lens:
            md.lens = str(lens).strip("\x00 ").strip() or None

        # Photography settings
        iso = exif_ifd.get(0x8827)
        if iso is not None:
            try:
                md.iso = int(iso if isinstance(iso, (int, float)) else iso[0])
            except Exception:
                pass

        f_num = exif_ifd.get(0x829D)
        if f_num is not None:
            md.f_number = _safe_float(f_num)

        exp_time = exif_ifd.get(0x829A)
        if exp_time is not None:
            md.exposure_time = _safe_exposure_str(exp_time)

        focal = exif_ifd.get(0x920A)
        if focal is not None:
            md.focal_length = _safe_float(focal)

        if raw_dt:
            dt = _parse_exif_dt(str(raw_dt), str(subsec) if subsec else None,
                                str(offset) if offset else None)
            if dt is not None:
                md.datetime_original = dt.isoformat()
                md.has_subsecond = bool(subsec)
    return md


def _from_exifread(path: Path) -> PhotoMetadata:
    md = PhotoMetadata()
    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)
    raw_dt = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    subsec = tags.get("EXIF SubSecTimeOriginal")
    if raw_dt:
        dt = _parse_exif_dt(str(raw_dt), str(subsec) if subsec else None, None)
        if dt is not None:
            md.datetime_original = dt.isoformat()
            md.has_subsecond = bool(subsec)
    make = tags.get("Image Make")
    model = tags.get("Image Model")
    if make:
        md.make = str(make).strip()
    if model:
        md.model = str(model).strip()
    lens = tags.get("EXIF LensModel")
    if lens:
        md.lens = str(lens).strip()

    iso = tags.get("EXIF ISOSpeedRatings") or tags.get("EXIF PhotographicSensitivity")
    if iso:
        try:
            md.iso = int(str(iso).split(",")[0].strip("[] "))
        except Exception:
            pass
    w = tags.get("EXIF ExifImageWidth") or tags.get("Image ImageWidth")
    h = tags.get("EXIF ExifImageLength") or tags.get("Image ImageLength")
    try:
        if w and h:
            md.width, md.height = int(str(w)), int(str(h))
    except Exception:
        pass

    f_num = tags.get("EXIF FNumber")
    if f_num:
        md.f_number = _safe_float(f_num)

    exp_time = tags.get("EXIF ExposureTime")
    if exp_time:
        md.exposure_time = _safe_exposure_str(exp_time)

    focal = tags.get("EXIF FocalLength")
    if focal:
        md.focal_length = _safe_float(focal)

    orientation = tags.get("Image Orientation")
    if orientation is not None:
        try:
            md.orientation = int(str(orientation.values[0])) if hasattr(orientation, "values") else 1
        except Exception:
            pass
    return md



def extract_metadata(path: Path) -> PhotoMetadata:
    """Best-effort metadata extraction. Never raises."""
    ext = path.suffix.lower()
    is_raw = ext in RAW_EXTS

    if not is_raw and HAS_PIL:
        try:
            return _from_pil(path)
        except Exception:
            pass

    if HAS_EXIFREAD:
        try:
            return _from_exifread(path)
        except Exception:
            pass

    return PhotoMetadata()
