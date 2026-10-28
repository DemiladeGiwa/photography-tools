"""Background image loading for the UI.

Decoding happens in worker threads; Tk ``PhotoImage`` objects are only ever
created on the main thread (Tk is not thread-safe). Results are handed over
through a queue that the main loop polls.
"""

from __future__ import annotations

import io
import queue
import threading
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional, Tuple

import cv2
from PIL import Image, ImageTk

from ..core import image_loader
from ..core.constants import PREVIEW_MAX_DIM, THUMB_MAX_DIM


class LRU(OrderedDict):
    def __init__(self, maxsize: int):
        super().__init__()
        self.maxsize = maxsize

    def get_(self, k):
        if k in self:
            self.move_to_end(k)
            return self[k]
        return None

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > self.maxsize:
            self.popitem(last=False)


class LifoPool:
    """Worker threads that run the most recently requested job first.

    After a fast scroll the photos now on screen load straight away instead of
    queueing behind everything that scrolled past. The oldest jobs are dropped
    once ``limit`` are waiting; ``submit`` returns them so callers can re-request.
    """

    def __init__(self, workers: int, limit: int = 400, name: str = "thumbs"):
        self._jobs: deque = deque()
        self._cv = threading.Condition()
        self._limit = limit
        self._stopped = False
        for i in range(workers):
            threading.Thread(target=self._run, name=f"{name}-{i}", daemon=True).start()

    def submit(self, fn: Callable, *args) -> list:
        with self._cv:
            self._jobs.append((fn, args))
            dropped = [self._jobs.popleft() for _ in range(max(0, len(self._jobs) - self._limit))]
            self._cv.notify()
        return dropped

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._jobs and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                fn, args = self._jobs.pop()
            try:
                fn(*args)
            except Exception:
                pass

    def shutdown(self) -> None:
        with self._cv:
            self._stopped = True
            self._jobs.clear()
            self._cv.notify_all()


class ImageService:
    def __init__(self, root, workers: int = 4):
        self.root = root
        self.lib = None
        self.pool = LifoPool(workers)
        self.preview_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview")
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.thumb_src = LRU(2500)  # key -> PIL image (<= THUMB_MAX_DIM)
        self.thumb_tk = LRU(700)  # (key, box) -> PhotoImage
        self.previews = LRU(8)  # (key, max_dim) -> PIL image
        self._pending: set = set()
        self._preview_pending: Dict[tuple, list] = {}
        self._listeners: list = []
        self._lock = threading.Lock()
        self._generation = 0
        self.defer_uncached = False  # True while analysis runs: it will cache thumbnails itself
        root.after(30, self._poll)

    # ------------------------------------------------------------ library
    def set_library(self, lib) -> None:
        self.lib = lib
        self._generation += 1
        self.thumb_src.clear()
        self.thumb_tk.clear()
        self.previews.clear()
        self._pending.clear()
        self._preview_pending.clear()

    def invalidate(self, key: str) -> None:
        self.thumb_src.pop(key, None)
        for k in [k for k in self.thumb_tk if k[0] == key]:
            self.thumb_tk.pop(k, None)

    def add_listener(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------ thumbnails
    def thumb(self, photo, box: int, box_h: Optional[int] = None) -> Optional[ImageTk.PhotoImage]:
        """Return a PhotoImage fitting ``box`` x ``box_h`` (default square), or None (loading starts)."""
        box_h = box_h or box
        k = (photo.key, box, box_h)
        tkimg = self.thumb_tk.get_(k)
        if tkimg is not None:
            return tkimg
        src = self.thumb_src.get_(photo.key)
        if src is not None:
            tkimg = ImageTk.PhotoImage(_fit(src, box, box_h))
            self.thumb_tk.put(k, tkimg)
            return tkimg
        if photo.key not in self._pending:
            self._pending.add(photo.key)
            # While analysis runs it makes the thumbnails itself, so only look in the cache (a thumbnail
            # from an older analysis is still there) and don't decode the file a second time.
            cache_only = self.defer_uncached and photo.data is None and not photo.error
            for _fn, args in self.pool.submit(self._load_thumb, photo, self._generation, cache_only):
                self._pending.discard(args[0].key)
        return None

    def _load_thumb(self, photo, gen: int, cache_only: bool = False) -> None:
        img = None
        if gen == self._generation:  # skip work for a folder that's no longer open
            try:
                data = self.lib.thumbnail_bytes(photo) if self.lib is not None else None
                if data:
                    img = Image.open(io.BytesIO(data))
                    img.load()
                elif not cache_only:
                    bgr, _err = image_loader.load_bgr(photo.display, THUMB_MAX_DIM)
                    if bgr is not None:
                        img = image_loader.bgr_to_pil(bgr)
            except Exception:
                img = None
        self.q.put(("thumb", gen, photo.key, img))

    def cancel_pending(self) -> None:
        """Drop queued thumbnail jobs (e.g. after a big scroll jump)."""
        self._generation += 1
        self._pending.clear()

    # ------------------------------------------------------------ previews
    def preview(self, photo, max_dim: Optional[int], callback: Callable[[Optional[Image.Image]], None]) -> None:
        k = (photo.key, max_dim)
        img = self.previews.get_(k)
        if img is not None:
            callback(img)
            return
        if k in self._preview_pending:
            self._preview_pending[k].append(callback)
            return
        self._preview_pending[k] = [callback]
        gen = self._generation
        self.preview_pool.submit(self._load_preview, photo, max_dim, gen)

    def prefetch(self, photo, max_dim: Optional[int] = PREVIEW_MAX_DIM) -> None:
        k = (photo.key, max_dim)
        if self.previews.get_(k) is None and k not in self._preview_pending:
            self._preview_pending[k] = []
            self.preview_pool.submit(self._load_preview, photo, max_dim, self._generation)

    def _load_preview(self, photo, max_dim, gen) -> None:
        bgr, _err = image_loader.load_bgr(photo.display, max_dim)
        img = None
        if bgr is not None:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img.np_rgb = rgb  # lets the viewer resize it without converting back from PIL (~25 ms)
        self.q.put(("preview", gen, (photo.key, max_dim), img))

    # ------------------------------------------------------------ main-thread pump
    def _poll(self) -> None:
        changed = []
        try:
            for _ in range(80):
                kind, gen, key, img = self.q.get_nowait()
                if kind == "thumb":
                    if gen != self._generation:
                        continue  # from a folder that's no longer open (its key may exist again in the new one)
                    self._pending.discard(key)
                    if img is not None:
                        self.thumb_src.put(key, img)
                        changed.append(key)
                else:
                    cbs = self._preview_pending.pop(key, [])
                    if img is not None:
                        self.previews.put(key, img)
                    for cb in cbs:
                        try:
                            cb(img)
                        except Exception:
                            pass
        except queue.Empty:
            pass
        if changed:
            for fn in self._listeners:
                try:
                    fn(changed)
                except Exception:
                    pass
        try:
            self.root.after(30 if not changed else 15, self._poll)
        except Exception:
            pass

    def has_preview(self, photo, max_dim: Optional[int] = PREVIEW_MAX_DIM) -> bool:
        return (photo.key, max_dim) in self.previews

    def cached_thumb(self, photo) -> Optional[Image.Image]:
        """The grid thumbnail if it's already in memory (used as an instant stand-in while a preview loads)."""
        return self.thumb_src.get(photo.key)

    def shutdown(self) -> None:
        self.pool.shutdown()
        self.preview_pool.shutdown(wait=False, cancel_futures=True)


def _fit(img: Image.Image, box: int, box_h: int) -> Image.Image:
    w, h = img.size
    s = min(box / w, box_h / h)
    if s >= 1:
        return img
    return img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)


def fit_size(w: int, h: int, bw: int, bh: int, allow_upscale: bool = False) -> Tuple[int, int]:
    s = min(bw / max(1, w), bh / max(1, h))
    if not allow_upscale:
        s = min(1.0, s)
    return max(1, int(w * s)), max(1, int(h * s))
