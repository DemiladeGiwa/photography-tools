"""Visual theme, scaling helpers and small reusable widgets."""

from __future__ import annotations

import json
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- palette
BG = "#121316"        # window
PANEL = "#191a1e"     # side panels
SURF = "#202227"      # cards / inputs
SURF2 = "#2a2c33"     # hover / raised
LINE = "#2f3138"
TEXT = "#e9e9ec"
MUTE = "#9a9ca6"
FAINT = "#5d606b"
ACC = "#e2a03f"       # warm amber accent (photography / film feel)
ACC_DIM = "#8a6428"
ACC_TEXT = "#16130d"
PICK = "#3fb96b"
REJECT = "#e5534b"
INFO = "#58a6ff"
WARN = "#d9a22a"
GRID_BG = "#0e0f11"
CELL = "#1b1c20"
CELL_SEL = "#3a3222"

LABEL_COLORS = {"Red": "#e5534b", "Yellow": "#e3b341", "Green": "#3fb96b", "Blue": "#4a8cff", "Purple": "#a371f7"}

SETTINGS_PATH = Path.home() / ".photo_sorter" / "settings.json"

_fonts: Dict[str, tkfont.Font] = {}
_scale = 1.0


def enable_high_dpi() -> None:
    """Must run before the Tk root exists (Windows only)."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes

                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def px(n: float) -> int:
    """Scale a pixel size for the current display DPI."""
    return int(round(n * _scale))


def font(name: str) -> tkfont.Font:
    return _fonts[name]


def setup(root: tk.Tk) -> None:
    """Create fonts and ttk styles. Call once after creating the root."""
    global _scale
    try:
        _scale = max(1.0, min(2.5, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        _scale = 1.0
    families = set(tkfont.families(root))
    ui = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter",
                           "Ubuntu", "DejaVu Sans") if f in families), "TkDefaultFont")
    mono = next((f for f in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono") if f in families), "TkFixedFont")
    spec = {
        "body": (ui, 10), "small": (ui, 9), "tiny": (ui, 8), "bold": (ui, 10, "bold"),
        "h1": (ui, 15, "bold"), "h2": (ui, 12, "bold"), "h3": (ui, 10, "bold"),
        "mono": (mono, 9), "big": (ui, 20, "bold"), "icon": (ui, 13),
    }
    for k, (fam, size, *w) in spec.items():
        _fonts[k] = tkfont.Font(root=root, family=fam, size=size, weight=(w[0] if w else "normal"))

    root.configure(bg=BG)
    root.option_add("*Font", _fonts["body"])
    root.option_add("*TCombobox*Listbox.background", SURF)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACC_DIM)
    root.option_add("*TCombobox*Listbox.font", _fonts["body"])

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, fieldbackground=SURF, bordercolor=LINE,
                lightcolor=LINE, darkcolor=LINE, troughcolor=SURF, focuscolor=ACC, font=_fonts["body"],
                selectbackground=ACC_DIM, selectforeground=TEXT, insertcolor=TEXT)
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("Card.TFrame", background=SURF)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    s.configure("Muted.TLabel", background=BG, foreground=MUTE)
    s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTE, font=_fonts["small"])
    s.configure("Card.TLabel", background=SURF, foreground=TEXT)
    s.configure("CardMuted.TLabel", background=SURF, foreground=MUTE, font=_fonts["small"])
    s.configure("H1.TLabel", background=BG, foreground=TEXT, font=_fonts["h1"])
    s.configure("H2.TLabel", background=BG, foreground=TEXT, font=_fonts["h2"])
    s.configure("CardH.TLabel", background=SURF, foreground=TEXT, font=_fonts["h3"])
    s.configure("PanelH.TLabel", background=PANEL, foreground=MUTE, font=_fonts["tiny"])

    pad = (px(12), px(6))
    s.configure("TButton", background=SURF2, foreground=TEXT, borderwidth=0, focusthickness=0,
                padding=pad, relief="flat")
    s.map("TButton", background=[("disabled", SURF), ("pressed", LINE), ("active", "#353842")],
          foreground=[("disabled", FAINT)])
    s.configure("Accent.TButton", background=ACC, foreground=ACC_TEXT, font=_fonts["bold"])
    s.map("Accent.TButton", background=[("disabled", ACC_DIM), ("pressed", "#c98a2e"), ("active", "#f0b356")],
          foreground=[("disabled", "#3a3020")])
    s.configure("Danger.TButton", background="#3a2023", foreground="#ffb4ae")
    s.map("Danger.TButton", background=[("active", "#4b2629"), ("pressed", "#5a2c30")])
    s.configure("Ghost.TButton", background=BG, foreground=MUTE, padding=(px(8), px(4)))
    s.map("Ghost.TButton", background=[("active", SURF)], foreground=[("active", TEXT)])
    s.configure("PanelGhost.TButton", background=PANEL, foreground=MUTE, padding=(px(8), px(4)))
    s.map("PanelGhost.TButton", background=[("active", SURF)], foreground=[("active", TEXT)])
    s.configure("Small.TButton", padding=(px(8), px(3)), font=_fonts["small"])

    s.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorbackground=SURF,
                indicatorforeground=ACC, padding=(0, px(2)))
    s.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", SURF2), ("active", SURF2)])
    s.configure("Panel.TCheckbutton", background=PANEL)
    s.map("Panel.TCheckbutton", background=[("active", PANEL)])
    s.configure("Card.TCheckbutton", background=SURF)
    s.map("Card.TCheckbutton", background=[("active", SURF)])
    s.configure("TRadiobutton", background=BG, foreground=TEXT, indicatorbackground=SURF, indicatorforeground=ACC)
    s.map("TRadiobutton", background=[("active", BG)])
    s.configure("Card.TRadiobutton", background=SURF)
    s.map("Card.TRadiobutton", background=[("active", SURF)])

    s.configure("TEntry", fieldbackground=SURF, foreground=TEXT, borderwidth=1, padding=px(5))
    s.map("TEntry", bordercolor=[("focus", ACC_DIM)])
    s.configure("TSpinbox", fieldbackground=SURF, foreground=TEXT, arrowcolor=MUTE, background=SURF2, padding=px(3))
    s.configure("TCombobox", fieldbackground=SURF, foreground=TEXT, arrowcolor=MUTE, background=SURF2, padding=px(4))
    s.map("TCombobox", fieldbackground=[("readonly", SURF)], foreground=[("readonly", TEXT)],
          selectbackground=[("readonly", SURF)], selectforeground=[("readonly", TEXT)])

    s.configure("Horizontal.TScale", background=SURF2, troughcolor=SURF, sliderthickness=px(14))
    s.configure("TProgressbar", background=ACC, troughcolor=SURF, borderwidth=0, thickness=px(4))
    s.configure("Vertical.TScrollbar", background=SURF, troughcolor=GRID_BG, arrowcolor=MUTE, borderwidth=0,
                gripcount=0, arrowsize=px(12))
    s.map("Vertical.TScrollbar", background=[("active", SURF2)])
    s.configure("Horizontal.TScrollbar", background=SURF, troughcolor=GRID_BG, arrowcolor=MUTE, borderwidth=0,
                gripcount=0, arrowsize=px(12))

    s.configure("Treeview", background=SURF, fieldbackground=SURF, foreground=TEXT, borderwidth=0,
                rowheight=px(24))
    s.configure("Treeview.Heading", background=PANEL, foreground=MUTE, relief="flat", font=_fonts["small"],
                padding=(px(6), px(4)))
    s.map("Treeview", background=[("selected", ACC_DIM)], foreground=[("selected", TEXT)])
    s.map("Treeview.Heading", background=[("active", SURF2)])
    s.configure("TNotebook", background=BG, borderwidth=0)
    s.configure("TNotebook.Tab", background=PANEL, foreground=MUTE, padding=(px(14), px(6)))
    s.map("TNotebook.Tab", background=[("selected", SURF)], foreground=[("selected", TEXT)])
    s.configure("TSeparator", background=LINE)


# ---------------------------------------------------------------- settings

def load_settings() -> Dict[str, Any]:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: Dict[str, Any]) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _legacy_recent() -> List[str]:
    try:
        return json.loads((Path.home() / ".photo_sorter" / "recent_folders.json").read_text(encoding="utf-8"))
    except Exception:
        return []


# ---------------------------------------------------------------- widgets

class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay: int = 450):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + px(8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.configure(bg=LINE)
        tk.Label(tw, text=self.text, bg=SURF2, fg=TEXT, font=font("small"), justify="left",
                 padx=px(8), pady=px(5), wraplength=px(320)).pack(padx=1, pady=1)
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


def tip(widget: tk.Widget, text: str) -> tk.Widget:
    Tooltip(widget, text)
    return widget


# ---------------------------------------------------------------- animation

def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(c1: str, c2: str, t: float) -> str:
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    a = [int(c1[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(c2[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{int(round(lerp(x, y, t))):02x}" for x, y in zip(a, b))


ANIMATIONS_ON = True
_running: Dict[Any, str] = {}


def animate(widget: tk.Misc, duration_ms: int, step: Callable[[float], None], done: Optional[Callable] = None,
            ease: Callable[[float], float] = ease_out_cubic, key: Any = None) -> None:
    """Run ``step(progress 0..1)`` at ~60 fps for ``duration_ms``. A new animation with the same key replaces the old."""
    if key is not None and key in _running:
        try:
            widget.after_cancel(_running.pop(key))
        except Exception:
            pass
    if not ANIMATIONS_ON or duration_ms <= 0:
        try:
            step(1.0)
        finally:
            if done:
                done()
        return
    import time as _t

    t0 = _t.perf_counter()

    def tick():
        t = min(1.0, (_t.perf_counter() - t0) * 1000.0 / duration_ms)
        try:
            step(ease(t))
        except tk.TclError:
            _running.pop(key, None)
            return
        if t < 1.0:
            job = widget.after(15, tick)
            if key is not None:
                _running[key] = job
        else:
            _running.pop(key, None)
            if done:
                done()

    tick()


def count_up(label: tk.Label, target: float, fmt: str = "{:.0f}", duration_ms: int = 500) -> None:
    """Animate a number in a label from its current value to ``target``."""
    try:
        start = float(getattr(label, "_count_value", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    label._count_value = target

    def step(t):
        label.configure(text=fmt.format(lerp(start, target, t)))

    animate(label, duration_ms, step, key=("count", str(label)))


def fade_in_window(root: tk.Tk, duration_ms: int = 220) -> None:
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        return

    def step(t):
        root.attributes("-alpha", t)

    root.after(30, lambda: animate(root, duration_ms, step, ease=ease_in_out))


class Toast:
    """Message card that slides up in the bottom-right corner, with an optional action (e.g. Undo)."""

    _current: Optional["Toast"] = None
    ICONS = {"ok": "✓", "error": "✕", "warn": "!", "info": "i"}

    def __init__(self, root: tk.Tk, message: str, kind: str = "info", ms: int = 3600,
                 action: Optional[Tuple[str, Callable]] = None):
        if Toast._current is not None:
            Toast._current.close(animated=False)
        Toast._current = self
        self.root = root
        color = {"ok": PICK, "error": REJECT, "warn": WARN}.get(kind, INFO)
        self.frame = tk.Frame(root, bg=LINE)
        inner = tk.Frame(self.frame, bg=SURF2)
        inner.pack(padx=1, pady=1, fill="both")
        tk.Label(inner, text=self.ICONS.get(kind, "i"), bg=color, fg="#101010", font=font("bold"),
                 width=2, padx=px(4)).pack(side="left", fill="y")
        msg = tk.Label(inner, text=message, bg=SURF2, fg=TEXT, font=font("body"), justify="left",
                       wraplength=px(380), padx=px(12), pady=px(10))
        msg.pack(side="left")
        for w in (self.frame, inner, msg):
            w.bind("<Button-1>", lambda e: self.close())
        if action:
            text, fn = action
            b = tk.Label(inner, text=text, bg=SURF2, fg=ACC, font=font("bold"), padx=px(12), cursor="hand2")
            b.pack(side="left", fill="y")
            b.bind("<Button-1>", lambda e: (self.close(), fn()))
        self._y = px(30)
        self._place()
        self.frame.lift()
        animate(self.frame, 260, lambda t: self._slide(lerp(px(30), -px(40), t)))
        self._after = root.after(ms, self.close)

    def _place(self):
        self.frame.place(relx=1.0, rely=1.0, x=-px(18), y=self._y, anchor="se")

    def _slide(self, y):
        self._y = y
        self._place()

    def close(self, animated: bool = True):
        try:
            self.root.after_cancel(self._after)
        except Exception:
            pass
        if Toast._current is self:
            Toast._current = None

        def gone():
            try:
                self.frame.destroy()
            except Exception:
                pass

        if animated and ANIMATIONS_ON:
            y0 = self._y
            animate(self.frame, 180, lambda t: self._slide(lerp(y0, px(60), t)), done=gone, ease=lambda t: t * t)
        else:
            gone()


class Card(tk.Frame):
    """Rounded-looking surface with an optional title and subtitle."""

    def __init__(self, parent, title: str = "", subtitle: str = "", **kw):
        super().__init__(parent, bg=SURF, highlightthickness=1, highlightbackground=LINE, **kw)
        self.body = tk.Frame(self, bg=SURF)
        if title:
            tk.Label(self, text=title, bg=SURF, fg=TEXT, font=font("h3"), anchor="w").pack(
                fill="x", padx=px(16), pady=(px(14), 0))
        if subtitle:
            sub = tk.Label(self, text=subtitle, bg=SURF, fg=MUTE, font=font("small"), anchor="w", justify="left",
                           wraplength=px(560))
            sub.pack(fill="x", padx=px(16), pady=(px(2), 0))
            bind_wrap(self, sub, px(36))
        self.body.pack(fill="both", expand=True, padx=px(16), pady=(px(10), px(14)))


def bind_wrap(container: tk.Widget, label: tk.Label, margin: int = 0) -> None:
    """Keep a label's wraplength matched to its container's width.

    Bound to the *container* (not the label) so a wrap change can't trigger an
    endless resize loop.
    """
    state = {"w": 0}

    def on_cfg(e):
        w = max(px(120), e.width - margin)
        if abs(w - state["w"]) > 4:
            state["w"] = w
            label.configure(wraplength=w)

    container.bind("<Configure>", on_cfg, add="+")


class ScrollFrame(tk.Frame):
    """Vertically scrollable container; put children in ``.inner``."""

    def __init__(self, parent, bg: str = BG, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        root = self.winfo_toplevel()
        if not getattr(root, "_scrollframe_wheel_bound", False):
            # One global handler that scrolls whichever ScrollFrame is under the mouse.
            root.bind_all("<MouseWheel>", _global_wheel, add="+")
            root.bind_all("<Button-4>", _global_wheel, add="+")
            root.bind_all("<Button-5>", _global_wheel, add="+")
            root._scrollframe_wheel_bound = True

    def scroll(self, e):
        if self.inner.winfo_height() <= self.canvas.winfo_height():
            return
        if getattr(e, "num", None) == 4:
            step = -3
        elif getattr(e, "num", None) == 5:
            step = 3
        else:
            step = int(-e.delta / 120 * 3) or (-1 if e.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")


def _global_wheel(e):
    try:
        w = e.widget.winfo_containing(e.x_root, e.y_root)
    except (tk.TclError, AttributeError, KeyError):
        return
    if w is None or w.winfo_class() in ("Listbox", "TCombobox", "Text"):
        return
    while w is not None:
        if isinstance(w, ScrollFrame):
            w.scroll(e)
            return
        w = w.master


class Segmented(tk.Frame):
    """A row of mutually exclusive pill buttons bound to a StringVar."""

    def __init__(self, parent, variable: tk.StringVar, options: List[tuple], command: Optional[Callable] = None,
                 bg: str = PANEL, compact: bool = False):
        super().__init__(parent, bg=bg)
        self.var, self.command, self.buttons = variable, command, {}
        for value, text in options:
            b = tk.Label(self, text=text, bg=SURF, fg=MUTE, font=font("small"), padx=px(7 if compact else 10),
                         pady=px(4), cursor="hand2")
            b.pack(side="left", padx=(0, 1))
            b.bind("<Button-1>", lambda e, v=value: self._select(v))
            self.buttons[value] = b
        variable.trace_add("write", lambda *a: self._refresh())
        self._refresh()

    def _select(self, v):
        self.var.set(v)
        if self.command:
            self.command()

    def _refresh(self):
        for v, b in self.buttons.items():
            on = self.var.get() == v
            b.configure(bg=ACC if on else SURF, fg=ACC_TEXT if on else MUTE)


class StarRating(tk.Frame):
    """Clickable 0-5 stars."""

    def __init__(self, parent, command: Callable[[int], None], bg: str = PANEL, size: str = "icon"):
        super().__init__(parent, bg=bg)
        self.command = command
        self.labels = []
        for i in range(1, 6):
            l = tk.Label(self, text="★", bg=bg, fg=FAINT, font=font(size), cursor="hand2", padx=1)
            l.pack(side="left")
            l.bind("<Button-1>", lambda e, n=i: self.command(0 if self.value == n else n))
            self.labels.append(l)
        self.value = 0

    def set(self, n: int):
        self.value = n
        for i, l in enumerate(self.labels, start=1):
            l.configure(fg=ACC if i <= n else FAINT)


def bar(parent, value: float, color: str, bg: str = PANEL, width: int = 160, height: int = 6) -> tk.Canvas:
    c = tk.Canvas(parent, width=px(width), height=px(height), bg=SURF, highlightthickness=0, bd=0)
    c.create_rectangle(0, 0, px(width) * max(0.0, min(1.0, value / 100.0)), px(height), fill=color, width=0)
    return c


def score_color(v: float) -> str:
    if v >= 75:
        return PICK
    if v >= 55:
        return WARN
    return REJECT
