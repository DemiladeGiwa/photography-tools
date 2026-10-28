"""Virtualised thumbnail grid on a single Canvas.

Only the rows currently on screen are drawn, so a folder of 20,000 photos
scrolls as smoothly as one with 20. Supports optional section headers (used
to show bursts/groups), multi-select (Ctrl / Shift), keyboard navigation and
live badges for flags, stars, labels, groups and blur warnings.
"""

from __future__ import annotations

import bisect
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.library import FLAG_PICK, FLAG_REJECT, Photo
from . import theme as T
from .theme import px


class ThumbGrid(tk.Frame):
    def __init__(self, parent, images, on_select: Callable[[Optional[Photo], List[Photo]], None],
                 on_open: Callable[[Photo], None], on_context: Optional[Callable[[tk.Event, Photo], None]] = None,
                 cell: int = 190, empty_text: str = "No photos"):
        super().__init__(parent, bg=T.GRID_BG)
        self.images = images
        self.on_select, self.on_open, self.on_context = on_select, on_open, on_context
        self.cell = cell
        self.empty_text = empty_text
        self.canvas = tk.Canvas(self, bg=T.GRID_BG, highlightthickness=0, bd=0, takefocus=1, yscrollincrement=1)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.sections: List[Tuple[Optional[str], List[Photo]]] = []
        self.order: List[Photo] = []
        self.index: Dict[str, int] = {}
        self.pos: Dict[str, Tuple[int, int]] = {}
        self.rows: List[Tuple[int, int, str, object]] = []  # (y, h, kind, payload)
        self.row_ys: List[int] = []
        self.total_h = 0
        self.cols = 1
        self.selected: Dict[str, Photo] = {}
        self.current: Optional[Photo] = None
        self.anchor: Optional[Photo] = None
        self._redraw_job = None
        self._full_redraw = True
        self._dirty_keys: set = set()
        self._wheel_px = 0.0
        self._width = 0
        self.header_click: Optional[Callable[[object], None]] = None
        self.hover_key: Optional[str] = None
        self._stamps: Dict[str, Tuple[str, float]] = {}
        self._stamp_job = None

        c = self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<Button-1>", self._click)
        c.bind("<Control-Button-1>", lambda e: self._click(e, "toggle"))
        c.bind("<Shift-Button-1>", lambda e: self._click(e, "range"))
        c.bind("<Double-Button-1>", self._double)
        c.bind("<Button-3>", self._context)
        c.bind("<Button-2>", self._context)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<Button-4>", lambda e: self._scroll_units(-1))
        c.bind("<Button-5>", lambda e: self._scroll_units(1))
        images.add_listener(self._on_thumbs_ready)

    # ------------------------------------------------------------ data
    def set_sections(self, sections: Sequence[Tuple[Optional[str], List[Photo]]], keep_scroll: bool = True) -> None:
        top = self.canvas.yview()[0] if keep_scroll else 0.0
        self.sections = [(t, list(ps)) for t, ps in sections]
        self.order = [p for _t, ps in self.sections for p in ps]
        self.index = {p.key: i for i, p in enumerate(self.order)}
        self.selected = {k: p for k, p in self.selected.items() if k in self.index}
        if self.current is not None and self.current.key not in self.index:
            self.current = None
        self._layout()
        if not keep_scroll:
            self.canvas.yview_moveto(0)
        else:
            self.canvas.yview_moveto(top)
        self.redraw()

    def set_photos(self, photos: Sequence[Photo], keep_scroll: bool = True) -> None:
        self.set_sections([(None, list(photos))], keep_scroll)

    def set_cell_size(self, size: int) -> None:
        key = self.current.key if self.current else None
        self.cell = size
        self._layout()
        if key:
            self.ensure_visible(self.index.get(key, 0))
        self.redraw()

    # ------------------------------------------------------------ layout
    @property
    def cell_w(self) -> int:
        return px(self.cell)

    @property
    def img_h(self) -> int:
        return int((self.cell_w - px(12)) * 0.72)

    @property
    def cell_h(self) -> int:
        return self.img_h + px(12) + px(40)

    def _layout(self) -> None:
        width = max(1, self.canvas.winfo_width())
        gap = px(6)
        self.cols = max(1, (width - gap) // (self.cell_w + gap))
        used = self.cols * (self.cell_w + gap) - gap
        x0 = max(gap, (width - used) // 2)
        y = px(8)
        self.rows, self.pos = [], {}
        header_h = px(34)
        for title, photos in self.sections:
            if title is not None:
                self.rows.append((y, header_h, "header", title))
                y += header_h
            for start in range(0, len(photos), self.cols):
                chunk = photos[start:start + self.cols]
                for i, p in enumerate(chunk):
                    self.pos[p.key] = (x0 + i * (self.cell_w + gap), y)
                self.rows.append((y, self.cell_h, "cells", chunk))
                y += self.cell_h + gap
            if title is not None:
                y += px(10)
        self.total_h = y + px(8)
        self.row_ys = [r[0] for r in self.rows]
        self.canvas.configure(scrollregion=(0, 0, width, max(self.total_h, self.canvas.winfo_height())))

    def _on_configure(self, e) -> None:
        if abs(e.width - self._width) > 2:
            self._width = e.width
            self._layout()
        self.redraw()

    # ------------------------------------------------------------ scrolling
    def _yview(self, *args) -> None:
        self.canvas.yview(*args)
        self.redraw()

    def _on_yscroll(self, a, b) -> None:
        self.vsb.set(a, b)

    def _scroll_px(self, amount: float) -> None:
        self._wheel_px += amount
        n = int(self._wheel_px)
        if n:
            self._wheel_px -= n
            self.canvas.yview_scroll(n, "units")  # 1 unit = 1 px
            self.redraw()

    def _scroll_units(self, n: int) -> None:
        self._scroll_px(n * self._notch_px())

    def _notch_px(self) -> float:
        return max(self.cell_h * 0.6, self.canvas.winfo_height() * 0.3)

    def _wheel(self, e) -> None:
        # Precision touchpads send many small deltas: scroll in proportion instead of a full step each.
        self._scroll_px(-e.delta / 120.0 * self._notch_px())

    def ensure_visible(self, idx: int) -> bool:
        """Scroll so the photo at ``idx`` is on screen. Returns True if it scrolled (a full redraw is queued)."""
        if not (0 <= idx < len(self.order)):
            return False
        x, y = self.pos.get(self.order[idx].key, (0, 0))
        top = self.canvas.canvasy(0)
        h = self.canvas.winfo_height()
        total = max(self.total_h, h)
        if y < top + px(4):
            # leave room for a section header above the first row of a group
            self.canvas.yview_moveto(max(0, (y - px(46)) / total))
        elif y + self.cell_h > top + h:
            self.canvas.yview_moveto(max(0, (y + self.cell_h + px(8) - h) / total))
        else:
            return False
        self.redraw()
        return True

    # ------------------------------------------------------------ drawing
    def redraw(self, *_):
        """Redraw everything on screen (after scrolling, layout or data changes)."""
        self._full_redraw = True
        if self._redraw_job is None:
            self._redraw_job = self.after_idle(self._draw)

    def redraw_keys(self, keys) -> None:
        """Redraw just these cells (hover, selection, a thumbnail arriving, a decision)."""
        self._dirty_keys.update(k for k in keys if k)
        if self._redraw_job is None:
            self._redraw_job = self.after_idle(self._draw)

    def _on_thumbs_ready(self, keys) -> None:
        mine = [k for k in keys if k in self.index]
        if mine:
            self.redraw_keys(mine)

    def _draw(self) -> None:
        self._redraw_job = None
        if not self._full_redraw and self.order:
            self._draw_dirty()
            return
        self._full_redraw = False
        self._dirty_keys.clear()
        c = self.canvas
        c.delete("all")
        if not self.order:
            w, h = c.winfo_width(), c.winfo_height()
            c.create_text(w // 2, h // 2 - px(10), text=self.empty_text, fill=T.MUTE, font=T.font("h2"),
                          justify="center", width=max(200, w - px(80)))
            return
        top = c.canvasy(0)
        bottom = top + c.winfo_height()
        i = max(0, bisect.bisect_right(self.row_ys, top) - 1)
        box = self.cell_w - px(12)
        while i < len(self.rows):
            y, h, kind, payload = self.rows[i]
            if y > bottom:
                break
            if y + h >= top:
                if kind == "header":
                    self._draw_header(y, payload)
                else:
                    for p in payload:
                        self._draw_cell(p, box)
            i += 1
        if self._stamps:
            self._draw_stamps(top, bottom)

    def _draw_dirty(self) -> None:
        keys, self._dirty_keys = self._dirty_keys, set()
        c = self.canvas
        top = c.canvasy(0)
        bottom = top + c.winfo_height()
        box = self.cell_w - px(12)
        for k in keys:
            i = self.index.get(k)
            if i is None:
                continue
            x, y = self.pos[k]
            if y + self.cell_h < top or y > bottom:
                continue
            c.delete(f"k{i}")
            self._draw_cell(self.order[i], box)
        c.tag_raise("stamp")

    # ------------------------------------------------------------ hover + decision stamps
    def _motion(self, e) -> None:
        p = self.photo_at(e.x, e.y)
        self._set_hover(p.key if p else None)

    def _set_hover(self, key: Optional[str]) -> None:
        if key != self.hover_key:
            old, self.hover_key = self.hover_key, key
            self.redraw_keys((old, key))

    STAMP_MS = 520
    STAMP_STYLE = {"pick": ("✓", T.PICK), "reject": ("✕", T.REJECT), "clear": ("○", T.MUTE)}

    def stamp(self, p: Photo, kind: str) -> None:
        """Animated badge that pops over a cell when it is picked / rejected / rated."""
        if not T.ANIMATIONS_ON or p.key not in self.pos:
            return
        import time

        self._stamps[p.key] = (kind, time.perf_counter())
        if self._stamp_job is None:
            self._stamp_tick()

    def _stamp_tick(self) -> None:
        import time

        now = time.perf_counter()
        self._stamps = {k: v for k, v in self._stamps.items() if (now - v[1]) * 1000 < self.STAMP_MS}
        c = self.canvas
        c.delete("stamp")  # only the stamps animate; the cells underneath stay as they are
        if self._stamps:
            top = c.canvasy(0)
            self._draw_stamps(top, top + c.winfo_height())
        self._stamp_job = self.after(16, self._stamp_tick) if self._stamps else None

    def _draw_stamps(self, top, bottom) -> None:
        import time

        c = self.canvas
        now = time.perf_counter()
        for key, (kind, t0) in self._stamps.items():
            if key not in self.pos:
                continue
            x, y = self.pos[key]
            if y + self.cell_h < top or y > bottom:
                continue
            t = min(1.0, (now - t0) * 1000 / self.STAMP_MS)
            cx, cy = x + self.cell_w // 2, y + px(6) + self.img_h // 2
            if kind.startswith("star"):
                n = int(kind[4:] or 0)
                symbol, color = ("★" * n if n else "☆"), T.ACC
            else:
                symbol, color = self.STAMP_STYLE.get(kind, ("•", T.MUTE))
            grow = T.ease_out_cubic(min(1.0, t / 0.45))
            fade = 0.0 if t < 0.55 else (t - 0.55) / 0.45
            r = px(14) + int(px(22) * grow)
            ring = T.lerp_color(color, T.CELL, fade)
            if not kind.startswith("star"):
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T.lerp_color("#101114", T.CELL, fade),
                              outline=ring, width=px(3), tags="stamp")
            size = max(8, int(T.font("big").cget("size") * (0.7 + 0.5 * grow) * (0.8 if kind.startswith("star") else 1)))
            c.create_text(cx, cy, text=symbol, fill=ring, font=(T.font("big").cget("family"), size, "bold"),
                          tags="stamp")

    def _draw_header(self, y: int, title) -> None:
        c = self.canvas
        w = c.winfo_width()
        text, sub = (title if isinstance(title, tuple) else (title, ""))[:2]
        c.create_line(px(12), y + px(30), w - px(12), y + px(30), fill=T.LINE)
        c.create_text(px(14), y + px(16), text=text, fill=T.TEXT, font=T.font("h3"), anchor="w", tags=("header",))
        if sub:
            c.create_text(w - px(14), y + px(16), text=sub, fill=T.MUTE, font=T.font("small"), anchor="e")

    def _draw_cell(self, p: Photo, box: int) -> None:
        c = self.canvas
        x, y = self.pos[p.key]
        tag = f"k{self.index[p.key]}"
        cw, ch = self.cell_w, self.cell_h
        sel = p.key in self.selected
        hov = p.key == self.hover_key
        cur = self.current is not None and self.current.key == p.key
        c.create_rectangle(x, y, x + cw, y + ch, fill=T.CELL_SEL if sel else ("#24252b" if hov else T.CELL),
                           outline=T.ACC if cur else (T.ACC_DIM if sel else T.CELL), width=px(2) if cur else 1, tags=tag)
        img = self.images.thumb(p, box, self.img_h)
        cx, cy = x + cw // 2, y + px(6) + self.img_h // 2
        if img is not None:
            c.create_image(cx, cy, image=img, tags=tag)
            iw, ih = img.width(), img.height()
        else:
            a = max(0.4, min(2.5, p.aspect))
            iw, ih = box, int(box / a)
            if ih > self.img_h:
                ih = self.img_h
                iw = int(ih * a)
            c.create_rectangle(cx - iw // 2, cy - ih // 2, cx + iw // 2, cy + ih // 2, fill=T.SURF, outline="", tags=tag)
            if p.error:
                c.create_text(cx, cy, text="Can't read file", fill=T.REJECT, font=T.font("tiny"), tags=tag)
        if p.flag == FLAG_REJECT:
            c.create_rectangle(cx - iw // 2, cy - ih // 2, cx + iw // 2, cy + ih // 2, fill="#000000",
                               stipple="gray50", outline="", tags=tag)
        # --- badges over the image
        bx, by = cx - iw // 2 + px(5), cy - ih // 2 + px(5)
        if p.flag == FLAG_PICK:
            self._pill(bx, by, "✓ PICK", T.PICK, "#0b1f12", tag=tag)
        elif p.flag == FLAG_REJECT:
            self._pill(bx, by, "✕ REJECT", T.REJECT, "#2a0b0a", tag=tag)
        tx = cx + iw // 2 - px(5)
        if p.has_raw:
            self._pill(tx, by, p.type_label, "#26282e", T.MUTE, anchor="ne", tag=tag)
        ty = cy + ih // 2 - px(5)
        if p.is_best:
            self._pill(bx, ty, "★ BEST", T.ACC, T.ACC_TEXT, anchor="sw", tag=tag)
        elif p.group_id:
            self._pill(bx, ty, f"G{p.group_id}", "#26282e", T.MUTE, anchor="sw", tag=tag)
        if p.is_blurry:
            self._pill(tx, ty, "BLUR", "#3a1d1d", "#ff9d97", anchor="se", tag=tag)
        # --- info strip
        iy = y + px(6) + self.img_h + px(6)
        name = p.name if len(p.name) <= 26 else p.name[:12] + "…" + p.name[-12:]
        c.create_text(x + px(8), iy + px(6), text=name, fill=T.TEXT if p.flag != FLAG_REJECT else T.FAINT,
                      font=T.font("small"), anchor="w", tags=tag)
        stars = "★" * p.rating
        if stars:
            c.create_text(x + px(8), iy + px(22), text=stars, fill=T.ACC, font=T.font("small"), anchor="w", tags=tag)
        if p.quality:
            q = p.quality_score
            c.create_text(x + cw - px(8), iy + px(22), text=f"{q:.0f}", fill=T.score_color(q),
                          font=T.font("bold"), anchor="e", tags=tag)
        elif not p.analyzed and not p.error:
            c.create_text(x + cw - px(8), iy + px(22), text="…", fill=T.FAINT, font=T.font("small"), anchor="e", tags=tag)
        if p.label:
            col = T.LABEL_COLORS.get(p.label, T.MUTE)
            c.create_rectangle(x + 1, y + ch - px(4), x + cw - 1, y + ch - 1, fill=col, outline="", tags=tag)

    def _pill(self, x, y, text, bg, fg, anchor="nw", tag: str = "") -> None:
        c = self.canvas
        t = c.create_text(x + px(6) if "w" in anchor else x - px(6), y + px(2) if "n" in anchor else y - px(2),
                          text=text, fill=fg, font=T.font("tiny"), anchor=anchor, tags=tag)
        x1, y1, x2, y2 = c.bbox(t)
        r = c.create_rectangle(x1 - px(5), y1 - px(1), x2 + px(5), y2 + px(1), fill=bg, outline="", tags=tag)
        c.tag_raise(t, r)

    # ------------------------------------------------------------ hit testing / selection
    def photo_at(self, ex: int, ey: int) -> Optional[Photo]:
        x, y = self.canvas.canvasx(ex), self.canvas.canvasy(ey)
        i = bisect.bisect_right(self.row_ys, y) - 1
        if i < 0:
            return None
        ry, rh, kind, payload = self.rows[i]
        if kind != "cells" or y > ry + rh:
            return None
        for p in payload:
            px0, _ = self.pos[p.key]
            if px0 <= x <= px0 + self.cell_w:
                return p
        return None

    def header_at(self, ey: int):
        y = self.canvas.canvasy(ey)
        i = bisect.bisect_right(self.row_ys, y) - 1
        if i >= 0 and self.rows[i][2] == "header" and y <= self.rows[i][0] + self.rows[i][1]:
            return self.rows[i][3]
        return None

    def _click(self, e, mode: str = "single") -> None:
        self.canvas.focus_set()
        p = self.photo_at(e.x, e.y)
        if p is None:
            h = self.header_at(e.y)
            if h is not None and self.header_click:
                self.header_click(h)
            return
        self.select(p, mode)

    def _double(self, e) -> None:
        p = self.photo_at(e.x, e.y)
        if p is not None:
            self.on_open(p)

    def _context(self, e) -> None:
        p = self.photo_at(e.x, e.y)
        if p is None:
            return
        if p.key not in self.selected:
            self.select(p, "single")
        if self.on_context:
            self.on_context(e, p)

    def select(self, p: Photo, mode: str = "single", notify: bool = True) -> None:
        before = set(self.selected) | ({self.current.key} if self.current else set())
        if mode == "toggle":
            if p.key in self.selected and len(self.selected) > 1:
                del self.selected[p.key]
            else:
                self.selected[p.key] = p
            self.anchor = p
        elif mode == "range" and self.anchor is not None and self.anchor.key in self.index:
            a, b = sorted((self.index[self.anchor.key], self.index[p.key]))
            self.selected = {q.key: q for q in self.order[a:b + 1]}
        else:
            self.selected = {p.key: p}
            self.anchor = p
        self.current = p
        if not self.ensure_visible(self.index[p.key]):  # no scroll: only the cells that changed
            changed = before | set(self.selected) | {p.key}
            if len(changed) > 300:
                self.redraw()
            else:
                self.redraw_keys(changed)
        if notify:
            self.on_select(self.current, self.selected_photos())

    def select_all(self) -> None:
        self.selected = {p.key: p for p in self.order}
        if self.current is None and self.order:
            self.current = self.order[0]
        self.redraw()
        self.on_select(self.current, self.selected_photos())

    def selected_photos(self) -> List[Photo]:
        return sorted(self.selected.values(), key=lambda p: self.index.get(p.key, 0))

    def move(self, dx: int = 0, dy: int = 0, extend: bool = False) -> None:
        if not self.order:
            return
        if self.current is None or self.current.key not in self.index:
            self.select(self.order[0])
            return
        i = self.index[self.current.key]
        if dy:
            j = self._vertical_neighbour(i, dy)
        else:
            j = max(0, min(len(self.order) - 1, i + dx))
        if j != i:
            self.select(self.order[j], "range" if extend else "single")

    def _vertical_neighbour(self, i: int, dy: int) -> int:
        p = self.order[i]
        x, y = self.pos[p.key]
        target_y = y + dy * (self.cell_h + px(6))
        best, best_d = i, None
        for j in range(max(0, i - self.cols * 3 - 2), min(len(self.order), i + self.cols * 3 + 3)):
            q = self.order[j]
            qx, qy = self.pos[q.key]
            if (dy > 0 and qy <= y) or (dy < 0 and qy >= y):
                continue
            d = abs(qy - target_y) * 10 + abs(qx - x)
            if best_d is None or d < best_d:
                best, best_d = j, d
        return best

    def focus_photo(self, p: Photo) -> None:
        if p.key in self.index:
            self.select(self.order[self.index[p.key]], "single")

    def next_after(self, p: Photo) -> Optional[Photo]:
        i = self.index.get(p.key)
        if i is None or i + 1 >= len(self.order):
            return None
        return self.order[i + 1]
