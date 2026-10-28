"""Loupe (single photo) and Compare (2-4 photos) views with synced zoom/pan."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from ..core import blur_detector
from ..core.constants import PREVIEW_MAX_DIM
from ..core.library import FLAG_PICK, FLAG_REJECT, Photo
from . import theme as T
from .theme import px

def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


ZOOM_STEPS = [0.125, 0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0]


def _fit_resize(img: Image.Image, size) -> Image.Image:
    """Resize for display. OpenCV's area filter is ~6x faster than Pillow's LANCZOS here
    (~15 ms vs ~100 ms for a 2400 px preview) and looks the same when shrinking."""
    if img.size == tuple(size):
        return img
    a = getattr(img, "np_rgb", None)  # set by ImageService on decoded previews
    if a is None:
        a = np.asarray(img.convert("RGB"))
    shrinking = size[0] < img.width
    return Image.fromarray(cv2.resize(a, tuple(size), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR))


class ViewState:
    """Zoom/pan shared between panes (so Compare panes move together)."""

    def __init__(self):
        self.zoom: Optional[float] = None  # None = fit, else display px per full-res px
        self.center = (0.5, 0.5)
        self.overlay: Optional[str] = None  # None | "heatmap" | "clip"
        self.listeners: List[Callable[[], None]] = []

    def changed(self):
        for fn in list(self.listeners):
            fn()


class ImagePane(tk.Canvas):
    def __init__(self, parent, images, state: ViewState, on_click: Optional[Callable[["ImagePane"], None]] = None,
                 show_info: bool = True):
        super().__init__(parent, bg=T.GRID_BG, highlightthickness=0, bd=0)
        self.images, self.state, self.on_click = images, state, on_click
        self.photo: Optional[Photo] = None
        self.preview: Optional[Image.Image] = None
        self.full: Optional[Image.Image] = None
        self.heat_preview: Optional[Image.Image] = None
        self._tk = None
        self._drag = None
        self._moved = False
        self.active = False
        self.show_info = show_info
        self._job = None
        self._last_frame = None  # (photo key, canvas-sized PIL frame) for cross-fades
        self._last_set = 0.0
        self._fast_nav = False
        self._fade_ok = True
        self._fade_item = None
        self._fit_key = None  # what the cached fit-to-window frame in self._tk shows
        self._preview_job = None
        state.listeners.append(self.render_later)
        self.bind("<Configure>", lambda e: self.render_later())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self._zoom_step(1, e))
        self.bind("<Button-5>", lambda e: self._zoom_step(-1, e))

    # ------------------------------------------------------------ data
    def set_photo(self, photo: Optional[Photo]) -> None:
        if photo is self.photo and self.preview is not None:
            self.render_later()
            return
        import time as _t

        now = _t.perf_counter()
        self._fast_nav = (now - self._last_set) < 0.28  # holding an arrow key
        # The cross-fade costs ~35 ms of frames; only worth it when browsing slowly, not when culling at pace.
        self._fade_ok = (now - self._last_set) > 0.7
        self._last_set = now
        self.photo = photo
        self.preview = self.full = self.heat_preview = None
        self.render_later()
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
            self._preview_job = None
        if photo is None:
            return
        if (self._fast_nav and not self.images.has_preview(photo)
                and self.images.cached_thumb(photo) is not None):
            # Arrow key held down: show the thumbnail now and only decode the big preview once you stop,
            # instead of queueing a decode for every photo flicked past.
            self._preview_job = self.after(160, lambda p=photo: self._request_preview(p))
        else:
            self._request_preview(photo)

    def _request_preview(self, p: Photo) -> None:
        self._preview_job = None
        if p is not self.photo:
            return
        self.images.preview(p, PREVIEW_MAX_DIM, lambda img, p=p: self._got_preview(p, img))
        if self.state.zoom is not None:
            self._want_full()

    def _got_preview(self, p, img) -> None:
        if p is self.photo:
            self.preview = img
            self.heat_preview = None
            self.render_later()

    def _want_full(self) -> None:
        p = self.photo
        if p is not None and self.full is None:
            self.images.preview(p, None, lambda img, p=p: self._got_full(p, img))

    def _got_full(self, p, img) -> None:
        if p is self.photo and img is not None:
            self.full = img
            self.render_later()

    @property
    def full_size(self):
        if self.full is not None:
            return self.full.size
        p = self.photo
        if self.preview is not None:
            w, h = self.preview.size
            if p is not None and p.meta.width and p.meta.height:
                mw, mh = p.meta.width, p.meta.height
                if (mw > mh) != (w > h):
                    mw, mh = mh, mw
                if mw >= w:
                    return mw, mh
            return w, h
        return 1, 1

    # ------------------------------------------------------------ rendering
    def render_later(self) -> None:
        if self._job is None:
            self._job = self.after_idle(self.render)

    def render(self) -> None:
        self._job = None
        self.delete("all")
        W, H = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self.photo is None:
            return
        src = self.preview
        if src is None:
            self._render_stand_in(W, H)
            self._draw_info(W, H)
            return
        if self.state.overlay == "heatmap":
            src = self._heat()
        fw, fh = self.full_size
        st = self.state
        if st.zoom is None:
            key = (self.photo.key, W, H, id(src), st.overlay, fw)
            if key == self._fit_key and self._tk is not None:
                # Same picture at the same size (a pick, a rating, a flash): reuse the frame, skip the resize.
                self._fade_item = self.create_image(0, 0, image=self._tk, anchor="nw")
            else:
                s = min(W / src.width, H / src.height, fw / src.width)  # never beyond true 100%
                dw, dh = max(1, int(src.width * s)), max(1, int(src.height * s))
                img = self._clip_overlay(_fit_resize(src, (dw, dh)))
                frame = Image.new("RGB", (W, H), _rgb(T.GRID_BG))
                frame.paste(img, ((W - dw) // 2, (H - dh) // 2))
                prev = self._last_frame
                self._last_frame = (self.photo.key, frame)
                self._tk = ImageTk.PhotoImage(frame)
                self._fit_key = key
                self._fade_item = self.create_image(0, 0, image=self._tk, anchor="nw")
                if (prev is not None and prev[0] != self.photo.key and prev[1].size == frame.size
                        and T.ANIMATIONS_ON and self._fade_ok):
                    self._crossfade(prev[1], frame)
        else:
            self._fit_key = None
            use_full = self.full is not None and self.state.overlay != "heatmap"
            base = self.full if use_full else src
            f = base.width / fw  # base px per full-res px
            vw, vh = W / st.zoom, H / st.zoom  # viewport in full-res px
            cx, cy = st.center[0] * fw, st.center[1] * fh
            cx = min(max(cx, min(vw / 2, fw / 2)), max(fw - vw / 2, fw / 2))
            cy = min(max(cy, min(vh / 2, fh / 2)), max(fh - vh / 2, fh / 2))
            st.center = (cx / fw, cy / fh)
            x0, y0 = max(0, cx - vw / 2), max(0, cy - vh / 2)
            x1, y1 = min(fw, cx + vw / 2), min(fh, cy + vh / 2)
            crop = base.crop((int(x0 * f), int(y0 * f), max(int(x0 * f) + 1, int(x1 * f)), max(int(y0 * f) + 1, int(y1 * f))))
            dw, dh = max(1, int((x1 - x0) * st.zoom)), max(1, int((y1 - y0) * st.zoom))
            crop = crop.resize((dw, dh), Image.NEAREST if st.zoom >= 2 else Image.BILINEAR)
            crop = self._clip_overlay(crop)
            self._tk = ImageTk.PhotoImage(crop)
            ox = (W - dw) // 2 if dw < W else 0
            oy = (H - dh) // 2 if dh < H else 0
            self.create_image(ox, oy, image=self._tk, anchor="nw")
            label = f"{st.zoom * 100:.0f}%" + ("" if use_full or self.full is not None else " (loading full size…)")
            self._badge(W - px(10), H - px(10), label, anchor="se")
        if self.active:
            self.create_rectangle(1, 1, W - 2, H - 2, outline=T.ACC, width=px(2))
        self._draw_info(W, H)

    def _render_stand_in(self, W: int, H: int) -> None:
        """While the preview decodes, show the grid thumbnail scaled up so the photo appears at once."""
        thumb = self.images.cached_thumb(self.photo) if self.photo is not None else None
        if thumb is None:
            self.create_text(W // 2, H // 2, text="Loading…", fill=T.MUTE, font=T.font("body"))
            return
        s = min(W / thumb.width, H / thumb.height)
        dw, dh = max(1, int(thumb.width * s)), max(1, int(thumb.height * s))
        self._tk = ImageTk.PhotoImage(thumb.resize((dw, dh), Image.BILINEAR))
        self._fit_key = None
        self._last_frame = None  # the sharp preview replaces this without a cross-fade back to the old photo
        self._fade_item = None
        self.create_image(W // 2, H // 2, image=self._tk)

    def _crossfade(self, old: Image.Image, new: Image.Image) -> None:
        item = self._fade_item
        steps = {"n": 0}

        def step(t):
            if self._fade_item != item or t >= 1.0:
                return
            steps["n"] += 1
            self._tk_fade = ImageTk.PhotoImage(Image.blend(old, new, t))
            try:
                self.itemconfigure(item, image=self._tk_fade)
            except tk.TclError:
                pass

        def done():
            try:
                if self._fade_item == item:
                    self.itemconfigure(item, image=self._tk)
            except tk.TclError:
                pass

        T.animate(self, 150, step, done=done, ease=T.ease_in_out, key=("fade", id(self)))

    def flash(self, kind: str) -> None:
        """Lightroom-style confirmation that pops in the middle of the photo."""
        if not T.ANIMATIONS_ON:
            return
        if kind.startswith("star"):
            n = int(kind[4:] or 0)
            symbol, color = ("★" * n if n else "No stars"), T.ACC
        else:
            symbol, color = {"pick": ("✓  Pick", T.PICK), "reject": ("✕  Reject", T.REJECT),
                             "clear": ("Unflagged", T.MUTE)}.get(kind, ("•", T.MUTE))
        fam = T.font("big").cget("family")

        def step(t):
            self.delete("flash")
            W, H = self.winfo_width(), self.winfo_height()
            if t < 0.25:
                scale = T.ease_out_cubic(t / 0.25)
            elif t < 0.75:
                scale = 1.0
            else:
                scale = max(0.0, 1 - T.ease_in_out((t - 0.75) / 0.25))
            if scale <= 0.02:
                return
            size = max(6, int(px(22) * scale))
            tid = self.create_text(W // 2, H // 2, text=symbol, fill=color, font=(fam, size, "bold"), tags="flash")
            b = self.bbox(tid)
            if b:
                pad = int(px(18) * scale)
                r = self.create_rectangle(b[0] - pad, b[1] - pad // 2, b[2] + pad, b[3] + pad // 2,
                                          fill="#121316", outline=color, width=max(1, px(2)), tags="flash")
                self.tag_raise(tid, r)

        T.animate(self, 700, step, done=lambda: self.delete("flash"), ease=lambda t: t, key=("flash", id(self)))

    def _heat(self) -> Image.Image:
        if self.heat_preview is None and self.preview is not None and self.photo is not None and self.photo.blur:
            bgr = cv2.cvtColor(np.asarray(self.preview), cv2.COLOR_RGB2BGR)
            over = blur_detector.generate_heatmap_overlay(bgr, self.photo.blur.heatmap,
                                                          threshold=self.photo.blur.threshold)
            self.heat_preview = Image.fromarray(cv2.cvtColor(over, cv2.COLOR_BGR2RGB))
        return self.heat_preview or self.preview

    def _clip_overlay(self, img: Image.Image) -> Image.Image:
        if self.state.overlay != "clip":
            return img
        a = np.asarray(img.convert("RGB")).copy()
        hi = a.max(axis=2) >= 250
        lo = a.max(axis=2) <= 6
        a[hi] = (255, 40, 60)
        a[lo] = (40, 110, 255)
        return Image.fromarray(a)

    def _badge(self, x, y, text, anchor="nw", fg=T.TEXT, bg="#1c1d22"):
        t = self.create_text(x, y, text=text, fill=fg, font=T.font("small"), anchor=anchor)
        b = self.bbox(t)
        r = self.create_rectangle(b[0] - px(7), b[1] - px(4), b[2] + px(7), b[3] + px(4), fill=bg, outline="")
        self.tag_raise(t, r)
        return b

    def _draw_info(self, W, H) -> None:
        p = self.photo
        if not self.show_info or p is None:
            return
        parts = []
        if p.flag == FLAG_PICK:
            self._badge(px(14), px(14), "✓ PICK", fg="#0b1f12", bg=T.PICK)
        elif p.flag == FLAG_REJECT:
            self._badge(px(14), px(14), "✕ REJECT", fg="#ffffff", bg=T.REJECT)
        if p.rating:
            parts.append("★" * p.rating)
        if p.quality:
            parts.append(f"Quality {p.quality_score:.0f}  ·  Sharp {p.sharpness_score:.0f}  ·  Exposure {p.exposure_score:.0f}")
        if p.is_best:
            parts.append("Best of group")
        if parts:
            self._badge(px(14), H - px(12), "   ".join(parts), anchor="sw")

    # ------------------------------------------------------------ interaction
    def _press(self, e) -> None:
        self._drag = (e.x, e.y, self.state.center)
        self._moved = False
        if self.on_click:
            self.on_click(self)

    def _motion(self, e) -> None:
        if self._drag is None or self.state.zoom is None:
            return
        x0, y0, (u, v) = self._drag
        if abs(e.x - x0) + abs(e.y - y0) > 3:
            self._moved = True
        fw, fh = self.full_size
        z = self.state.zoom
        self.state.center = (u - (e.x - x0) / z / fw, v - (e.y - y0) / z / fh)
        self.state.changed()

    def _release(self, e) -> None:
        if self._drag is not None and not self._moved:
            self.toggle_zoom(e)
        self._drag = None

    def toggle_zoom(self, e=None) -> None:
        st = self.state
        if st.zoom is None:
            if e is not None and self.preview is not None:
                st.center = self._point_to_uv(e.x, e.y)
            st.zoom = 1.0
            self._want_full()
        else:
            st.zoom = None
        st.changed()

    def _point_to_uv(self, x, y):
        W, H = self.winfo_width(), self.winfo_height()
        src = self.preview
        s = min(W / src.width, H / src.height)
        dw, dh = src.width * s, src.height * s
        u = (x - (W - dw) / 2) / dw
        v = (y - (H - dh) / 2) / dh
        return (min(1, max(0, u)), min(1, max(0, v)))

    def _wheel(self, e) -> None:
        self._zoom_step(1 if e.delta > 0 else -1, e)

    def _zoom_step(self, direction: int, e=None) -> None:
        st = self.state
        W, H = self.winfo_width(), self.winfo_height()
        fw, fh = self.full_size
        fit = min(W / fw, H / fh)
        cur = st.zoom if st.zoom is not None else fit
        if direction > 0:
            nxt = next((z for z in ZOOM_STEPS if z > cur * 1.01), ZOOM_STEPS[-1])
        else:
            nxt = next((z for z in reversed(ZOOM_STEPS) if z < cur * 0.99), None)
            if nxt is None or nxt <= fit:
                st.zoom = None
                st.changed()
                return
        if st.zoom is None and e is not None and self.preview is not None:
            st.center = self._point_to_uv(e.x, e.y)
        st.zoom = nxt
        if nxt >= 0.5:
            self._want_full()
        st.changed()


class Filmstrip(tk.Canvas):
    """A strip of the photos either side of the one in the loupe. Click (or scroll) to move."""

    CELL = 96

    def __init__(self, parent, images, on_pick: Callable[[Photo], None]):
        super().__init__(parent, bg=T.PANEL, height=px(76), highlightthickness=0, bd=0)
        self.images, self.on_pick = images, on_pick
        self.order: List[Photo] = []
        self.idx = 0
        self._job = None
        self._hits: List[tuple] = []
        self._shown: set = set()
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<MouseWheel>", lambda e: self._step(-1 if e.delta > 0 else 1))
        self.bind("<Button-4>", lambda e: self._step(-1))
        self.bind("<Button-5>", lambda e: self._step(1))
        images.add_listener(self._thumbs_ready)

    def set(self, order: List[Photo], idx: int) -> None:
        self.order, self.idx = order, idx
        self.redraw()

    def redraw(self) -> None:
        if self._job is None:
            self._job = self.after_idle(self._draw)

    def _thumbs_ready(self, keys) -> None:
        if self._shown and any(k in self._shown for k in keys):
            self.redraw()

    def _draw(self) -> None:
        self._job = None
        self.delete("all")
        self._hits, self._shown = [], set()
        W, H = self.winfo_width(), self.winfo_height()
        if not self.order or W < 10:
            return
        cw = px(self.CELL)
        bw, bh = cw - px(8), H - px(16)
        side = W // cw // 2 + 1
        cy = H // 2
        for d in range(-side, side + 1):
            j = self.idx + d
            if not 0 <= j < len(self.order):
                continue
            p = self.order[j]
            x = W // 2 + d * cw
            self._shown.add(p.key)
            img = self.images.thumb(p, bw, bh)
            if img is not None:
                self.create_image(x, cy, image=img)
                iw, ih = img.width(), img.height()
            else:
                iw = bw
                ih = min(bh, int(bw / max(0.4, min(2.5, p.aspect))))
                self.create_rectangle(x - iw // 2, cy - ih // 2, x + iw // 2, cy + ih // 2, fill=T.SURF, outline="")
            x0, y0, x1, y1 = x - iw // 2, cy - ih // 2, x + iw // 2, cy + ih // 2
            if p.flag == FLAG_REJECT:
                self.create_rectangle(x0, y0, x1, y1, fill="#000000", stipple="gray50", outline="")
            mark = {FLAG_PICK: T.PICK, FLAG_REJECT: T.REJECT}.get(p.flag or "")
            if mark:
                self.create_rectangle(x0, y1 + px(2), x1, y1 + px(5), fill=mark, outline="")
            if p.rating:
                self.create_text(x1 - px(3), y0 + px(2), text="★" * p.rating, fill=T.ACC, font=T.font("tiny"),
                                 anchor="ne")
            if d == 0:
                self.create_rectangle(x0 - px(3), y0 - px(3), x1 + px(3), y1 + px(3), outline=T.ACC, width=px(2))
            self._hits.append((x - cw // 2, x + cw // 2, j))

    def _click(self, e) -> None:
        for x0, x1, j in self._hits:
            if x0 <= e.x < x1 and j != self.idx:
                self.on_pick(self.order[j])
                return

    def _step(self, d: int) -> None:
        j = self.idx + d
        if 0 <= j < len(self.order):
            self.on_pick(self.order[j])


class Loupe(tk.Frame):
    """Large single-photo view with zoom, overlays and keyboard culling."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.GRID_BG)
        self.app = app
        self.state = ViewState()
        bar = tk.Frame(self, bg=T.PANEL)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Back to grid", style="PanelGhost.TButton", command=app.close_viewer).pack(
            side="left", padx=px(6), pady=px(5))
        right = tk.Frame(bar, bg=T.PANEL)
        right.pack(side="right", padx=px(6))
        self.btn_heat = ttk.Button(right, text="Focus map (H)", style="PanelGhost.TButton",
                                   command=lambda: self.set_overlay("heatmap"))
        self.btn_heat.pack(side="left")
        T.tip(self.btn_heat, "Colours each part of the frame by sharpness: green = sharp, red = soft.")
        self.btn_clip = ttk.Button(right, text="Clipping (K)", style="PanelGhost.TButton",
                                   command=lambda: self.set_overlay("clip"))
        self.btn_clip.pack(side="left")
        T.tip(self.btn_clip, "Shows blown highlights in red and crushed shadows in blue.")
        ttk.Button(right, text="Fit / 100% (Z)", style="PanelGhost.TButton",
                   command=lambda: self.pane.toggle_zoom()).pack(side="left")
        self.title = tk.Label(bar, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("h3"))
        self.title.pack(side="left", padx=px(8))
        self.sub = tk.Label(bar, text="", bg=T.PANEL, fg=T.MUTE, font=T.font("small"))
        self.sub.pack(side="left", padx=px(4))
        self.strip = Filmstrip(self, app.images, on_pick=lambda p: app.views["cull"].grid.select(p))
        self.pane = ImagePane(self, app.images, self.state)
        if app.settings.get("filmstrip", True):
            self.strip.pack(side="bottom", fill="x")
        self.pane.pack(fill="both", expand=True)
        self.photo: Optional[Photo] = None

    def toggle_filmstrip(self) -> None:
        on = not self.strip.winfo_ismapped()
        if on:
            self.strip.pack(side="bottom", fill="x", before=self.pane)
        else:
            self.strip.pack_forget()
        self.app.settings["filmstrip"] = on

    def show(self, photo: Photo, index: int, total: int) -> None:
        self.photo = photo
        self.title.configure(text=photo.name)
        info = [f"{index + 1} of {total}"]
        if photo.capture_time:
            info.append(photo.capture_time.strftime("%Y-%m-%d %H:%M:%S"))
        summary = photo.meta.format_exif_summary()
        if summary != "No EXIF data":
            info.append(summary)
        self.sub.configure(text="   ·   ".join(info))
        self.pane.set_photo(photo)

    def refresh(self) -> None:
        self.pane.render_later()
        self.strip.redraw()

    def set_overlay(self, kind: str) -> None:
        self.state.overlay = None if self.state.overlay == kind else kind
        for b, k in ((self.btn_heat, "heatmap"), (self.btn_clip, "clip")):
            b.configure(style="Accent.TButton" if self.state.overlay == k else "PanelGhost.TButton")
        self.state.changed()


class CompareView(tk.Frame):
    """2-4 photos side by side. Click a pane to make it active; keys act on it."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.GRID_BG)
        self.app = app
        self.state = ViewState()
        bar = tk.Frame(self, bg=T.PANEL)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Back to grid", style="PanelGhost.TButton", command=app.close_viewer).pack(
            side="left", padx=px(6), pady=px(5))
        tk.Label(bar, text="Compare", bg=T.PANEL, fg=T.TEXT, font=T.font("h3")).pack(side="left", padx=px(8))
        tk.Label(bar, text="Click a photo to make it active (Tab = next) · P / X / 1-5 apply to it · "
                           "click or scroll to zoom all",
                 bg=T.PANEL, fg=T.MUTE, font=T.font("small")).pack(side="left", padx=px(6))
        self.body = tk.Frame(self, bg=T.GRID_BG)
        self.body.pack(fill="both", expand=True)
        self.panes: List[ImagePane] = []
        self.captions: List[tk.Label] = []
        self.photos: List[Photo] = []
        self.active = 0

    def show(self, photos: List[Photo]) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        for pane in self.panes:
            if pane.render_later in self.state.listeners:
                self.state.listeners.remove(pane.render_later)
        self.panes, self.captions = [], []
        self.photos = photos[:4]
        self.state.zoom, self.state.center = None, (0.5, 0.5)
        n = len(self.photos)
        cols = 2 if n == 4 else n
        rows = 2 if n == 4 else 1
        for i, p in enumerate(self.photos):
            cell = tk.Frame(self.body, bg=T.GRID_BG)
            cell.grid(row=i // cols, column=i % cols, sticky="nsew", padx=1, pady=1)
            cap = tk.Label(cell, text="", bg=T.PANEL, fg=T.TEXT, font=T.font("small"), anchor="w", padx=px(8),
                           pady=px(4))
            cap.pack(fill="x")
            pane = ImagePane(cell, self.app.images, self.state, on_click=self._activate)
            pane.pack(fill="both", expand=True)
            pane.set_photo(p)
            self.panes.append(pane)
            self.captions.append(cap)
        for c in range(cols):
            self.body.columnconfigure(c, weight=1, uniform="c")
        for r in range(rows):
            self.body.rowconfigure(r, weight=1, uniform="r")
        self.active = 0
        self._update()

    def _activate(self, pane: ImagePane) -> None:
        if pane in self.panes:
            self.active = self.panes.index(pane)
            self._update()
            self.app.on_viewer_active_changed(self.photos[self.active])

    def active_photo(self) -> Optional[Photo]:
        return self.photos[self.active] if self.photos else None

    def cycle(self, d: int) -> None:
        if self.photos:
            self.active = (self.active + d) % len(self.photos)
            self._update()

    def _update(self) -> None:
        best_q = max((p.quality_score for p in self.photos), default=0)
        for i, (pane, cap, p) in enumerate(zip(self.panes, self.captions, self.photos)):
            pane.active = (i == self.active)
            flag = {"pick": "  ✓ PICK", "reject": "  ✕ REJECT"}.get(p.flag or "", "")
            crown = "  ★ highest quality" if p.quality and p.quality_score == best_q and len(self.photos) > 1 else ""
            q = f"   Q {p.quality_score:.0f} · S {p.sharpness_score:.0f} · E {p.exposure_score:.0f}" if p.quality else ""
            cap.configure(text=f"{p.name}{q}{flag}{crown}", bg=T.ACC_DIM if i == self.active else T.PANEL)
            pane.render_later()

    def refresh(self) -> None:
        self._update()
