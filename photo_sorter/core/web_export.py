"""
Share-ready JPEG export: resize, sharpen for screens, optional watermark.

Works from RAW too (decoded on the fly). Originals are never touched; the new
files are journaled so the export can be undone from History.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import image_loader
from .file_operations import Journal, JournalEntry, Transaction
from .library import Library, Photo
from .organizer import TemplateContext, render, sanitize_component

SIZE_PRESETS = {
    "Instagram (1080 px)": 1080,
    "Web / email (2048 px)": 2048,
    "Full HD (1920 px)": 1920,
    "4K (3840 px)": 3840,
    "Full size": 0,
}

POSITIONS = ["bottom-right", "bottom-left", "top-right", "top-left", "center"]


@dataclass
class WebExportOptions:
    destination: Path
    long_edge: int = 2048  # 0 = original size
    quality: int = 88
    sharpen: bool = True
    keep_exif: bool = True
    watermark: str = ""
    position: str = "bottom-right"
    opacity: float = 0.6  # 0-1
    size_pct: float = 3.0  # text height as % of the long edge
    rename_template: str = ""


def _font(px_size: int) -> ImageFont.ImageFont:
    for name in ("segoeuisb.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf", "Helvetica.ttc",
                 "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px_size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px_size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def apply_watermark(img: Image.Image, text: str, position: str = "bottom-right", opacity: float = 0.6,
                    size_pct: float = 3.0) -> Image.Image:
    if not text.strip():
        return img
    base = img.convert("RGBA")
    W, H = base.size
    size = max(10, int(max(W, H) * size_pct / 100))
    font = _font(size)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    tw, th = x1 - x0, y1 - y0
    m = int(size * 0.9)
    pos = {
        "bottom-right": (W - tw - m, H - th - m), "bottom-left": (m, H - th - m),
        "top-right": (W - tw - m, m), "top-left": (m, m), "center": ((W - tw) // 2, (H - th) // 2),
    }.get(position, (W - tw - m, H - th - m))
    a = int(255 * max(0.0, min(1.0, opacity)))
    shadow = max(1, size // 18)
    d.text((pos[0] - x0 + shadow, pos[1] - y0 + shadow), text, font=font, fill=(0, 0, 0, int(a * 0.55)))
    d.text((pos[0] - x0, pos[1] - y0), text, font=font, fill=(255, 255, 255, a))
    return Image.alpha_composite(base, layer).convert("RGB")


def render_one(p: Photo, opts: WebExportOptions) -> Tuple[Optional[Image.Image], Optional[bytes], Optional[str]]:
    bgr, err = image_loader.load_bgr(p.display, opts.long_edge or None)
    if bgr is None:
        return None, None, err
    img = image_loader.bgr_to_pil(bgr)
    if opts.sharpen and opts.long_edge:
        img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=60, threshold=2))
    img = apply_watermark(img, opts.watermark, opts.position, opts.opacity, opts.size_pct)
    exif = None
    if opts.keep_exif:
        try:
            with Image.open(p.display) as src:
                ex = src.getexif()
                if ex:
                    ex[0x0112] = 1  # pixels are already upright
                    exif = ex.tobytes()
        except Exception:
            exif = None
    return img, exif, None


def full_quality_options(destination: Path, quality: int = 95) -> WebExportOptions:
    """Options for 'Save as JPG': original size, no sharpening or watermark, EXIF kept."""
    return WebExportOptions(destination=Path(destination), long_edge=0, quality=quality, sharpen=False,
                            keep_exif=True)


def save_as_jpg(lib: Library, photos: Sequence[Photo], destination: Path, quality: int = 95,
                progress_cb: Optional[Callable[[int, int, str], None]] = None,
                should_stop: Callable[[], bool] = lambda: False) -> Tuple[int, Journal, List[str]]:
    """Convert the given photos (RAW, HEIC, PNG, JPG...) to full-size JPEGs. Originals are untouched."""
    return export_web(lib, photos, full_quality_options(destination, quality), progress_cb, should_stop)


_SKIPPED = "skipped (stopped)"  # returned by a job that was stopped before it started


def _write_one(p: Photo, out: Path, opts: WebExportOptions) -> Optional[str]:
    """Render and save one JPEG. Returns an error message, or None when it worked."""
    img, exif, err = render_one(p, opts)
    if img is None:
        return err or "couldn't be read"
    kw = {"quality": int(opts.quality), "optimize": True, "progressive": True,
          "subsampling": 0 if opts.quality >= 90 else 2}
    if exif:
        kw["exif"] = exif
    try:
        img.save(out, "JPEG", **kw)
    except OSError as e:
        return str(e)
    return None


def export_workers(opts: WebExportOptions) -> int:
    # Full-size frames take ~100-150 MB each while being processed, so run fewer at once.
    cpus = os.cpu_count() or 2
    return max(1, min(3 if not opts.long_edge else 4, cpus - 1))


def export_web(
    lib: Library,
    photos: Sequence[Photo],
    opts: WebExportOptions,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Callable[[], bool] = lambda: False,
    max_workers: Optional[int] = None,
) -> Tuple[int, Journal, List[str]]:
    dest = Path(opts.destination)
    dest.mkdir(parents=True, exist_ok=True)
    ctx = TemplateContext()
    for i, p in enumerate(photos, start=1):
        ctx.seq[p.key] = i
    n = 0
    with Transaction(lib.root, kind="web-export", label=f"Share-ready JPEGs ({len(photos)}) to {dest.name}") as tx:
        # Names are reserved up front, in order, so running in parallel gives exactly the names
        # (and ' (1)' suffixes) a one-at-a-time run would.
        outs = []
        for p in photos:
            stem = sanitize_component(render(opts.rename_template, p, ctx)) if opts.rename_template else p.stem
            outs.append(tx.reserve(dest / f"{stem}.jpg"))

        def job(p: Photo, out: Path) -> Optional[str]:
            if should_stop():
                return _SKIPPED
            return _write_one(p, out, opts)

        with ThreadPoolExecutor(max_workers=max_workers or export_workers(opts),
                                thread_name_prefix="jpeg") as pool:
            futures = [pool.submit(job, p, out) for p, out in zip(photos, outs)]
            for i, (p, out, fut) in enumerate(zip(photos, outs, futures), start=1):
                try:
                    err = fut.result()
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
                    err = f"{type(e).__name__}: {e}"
                if err is _SKIPPED:
                    for f in futures[i:]:
                        f.cancel()
                    break
                if err:
                    tx.errors.append(f"{p.name}: {err}")
                else:
                    tx.journal.entries.append(JournalEntry(op="copy", src=str(p.display), dst=str(out), status="done"))
                    n += 1
                if progress_cb:
                    progress_cb(i, len(photos), p.name)
    return n, tx.journal, tx.errors

