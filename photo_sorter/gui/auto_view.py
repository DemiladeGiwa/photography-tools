"""Auto-Cull screen: preset-driven pick/reject suggestions you review before applying."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..core import presets, selection
from ..core.library import FLAG_PICK, FLAG_REJECT
from ..core.scoring import FOCUS_PROFILES
from . import theme as T
from .common import header
from .theme import px

class AutoCullView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.sf = T.ScrollFrame(self)
        self.sf.pack(fill="both", expand=True)
        root = self.sf.inner
        header(root, "Auto-Cull", "Let the analysis suggest picks and rejects. Nothing is moved or deleted - it only "
                                   "sets flags you can review in Cull. Photos you've decided by hand are kept.")
        cols = tk.Frame(root, bg=T.BG)
        cols.pack(fill="both", expand=True, padx=px(24), pady=(0, px(24)))
        cols.columnconfigure(0, weight=3, uniform="a")
        cols.columnconfigure(1, weight=2, uniform="a")
        left = tk.Frame(cols, bg=T.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        right = tk.Frame(cols, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")

        # --- preset
        c = T.Card(left, "Preset", "Start from a genre preset, then fine-tune.")
        c.pack(fill="x", pady=(0, px(12)))
        row = tk.Frame(c.body, bg=T.SURF)
        row.pack(fill="x")
        self.preset_var = tk.StringVar(value="Balanced General")
        self.preset_cb = ttk.Combobox(row, textvariable=self.preset_var, state="readonly", width=30)
        self.preset_cb.pack(side="left")
        self.preset_cb.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())
        ttk.Button(row, text="Save as preset…", style="Small.TButton", command=self.save_preset).pack(side="left", padx=px(8))
        self.preset_desc = tk.Label(c.body, text="", bg=T.SURF, fg=T.MUTE, font=T.font("small"), anchor="w",
                                    justify="left", wraplength=px(520))
        self.preset_desc.pack(fill="x", pady=(px(6), 0))

        # --- strategy
        c = T.Card(left, "How to choose picks")
        c.pack(fill="x", pady=(0, px(12)))
        self.strategy_var = tk.StringVar(value="best_of_groups")
        self.count_var = tk.IntVar(value=50)
        self.percent_var = tk.DoubleVar(value=20.0)
        self.minq_var = tk.DoubleVar(value=65.0)
        self.maxgroup_var = tk.IntVar(value=1)
        for key, text in selection.STRATEGIES.items():
            r = tk.Frame(c.body, bg=T.SURF)
            r.pack(fill="x", pady=1)
            ttk.Radiobutton(r, text=text, value=key, variable=self.strategy_var, style="Card.TRadiobutton",
                            command=self.update_preview).pack(side="left")
            if key == "top_n":
                ttk.Spinbox(r, from_=1, to=100000, width=7, textvariable=self.count_var,
                            command=self.update_preview).pack(side="left", padx=px(8))
            elif key == "top_percent":
                ttk.Spinbox(r, from_=1, to=100, width=5, textvariable=self.percent_var,
                            command=self.update_preview).pack(side="left", padx=px(8))
                tk.Label(r, text="%", bg=T.SURF, fg=T.MUTE).pack(side="left")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        tk.Label(r, text="Minimum quality for singles / threshold", bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(side="left")
        self.minq_label = tk.Label(r, text="", bg=T.SURF, fg=T.TEXT, font=T.font("bold"), width=4)
        self.minq_label.pack(side="right")
        ttk.Scale(c.body, from_=0, to=100, variable=self.minq_var, command=lambda v: self._changed()).pack(fill="x")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(8), 0))
        tk.Label(r, text="Max picks per group (Top N / Top %)", bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack(side="left")
        ttk.Spinbox(r, from_=0, to=20, width=4, textvariable=self.maxgroup_var, command=self.update_preview).pack(side="right")

        # --- what counts as good
        c = T.Card(left, "What counts as a good photo",
                   "Changing these re-scores every photo instantly (no re-analysis needed).")
        c.pack(fill="x", pady=(0, px(12)))
        self.sharp_w = tk.DoubleVar(value=60)
        self.profile_var = tk.StringVar(value="balanced")
        self.blur_thr = tk.DoubleVar(value=55)
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x")
        tk.Label(r, text="Sharpness", bg=T.SURF, fg=T.TEXT).pack(side="left")
        self.weight_label = tk.Label(r, text="", bg=T.SURF, fg=T.MUTE, font=T.font("small"))
        self.weight_label.pack(side="left", padx=px(8))
        tk.Label(r, text="Exposure", bg=T.SURF, fg=T.TEXT).pack(side="right")
        ttk.Scale(c.body, from_=0, to=100, variable=self.sharp_w, command=lambda v: self._changed(rescore=True)).pack(fill="x")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(10), 0))
        tk.Label(r, text="Focus profile", bg=T.SURF, fg=T.TEXT).pack(side="left")
        self.profile_cb = ttk.Combobox(r, state="readonly", values=list(FOCUS_PROFILES.values()), width=28)
        self.profile_cb.pack(side="right")
        self.profile_cb.bind("<<ComboboxSelected>>", lambda e: self._changed(rescore=True))
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(10), 0))
        tk.Label(r, text="Blur threshold", bg=T.SURF, fg=T.TEXT).pack(side="left")
        self.blur_label = tk.Label(r, text="", bg=T.SURF, fg=T.MUTE, font=T.font("small"))
        self.blur_label.pack(side="right")
        s = ttk.Scale(c.body, from_=20, to=90, variable=self.blur_thr, command=lambda v: self._changed(rescore=True))
        s.pack(fill="x")
        T.tip(s, "Sharpness (0-100) below which a photo counts as blurry. 42 = only obvious blur, 55 = balanced, "
                 "68 = strict.")

        # --- rejects
        c = T.Card(left, "Rejects")
        c.pack(fill="x", pady=(0, px(12)))
        self.rej_blurry = tk.BooleanVar(value=True)
        self.rej_extras = tk.BooleanVar(value=True)
        self.rej_rest = tk.BooleanVar(value=False)
        self.respect = tk.BooleanVar(value=True)
        self.pick_stars = tk.IntVar(value=0)
        for var, text in ((self.rej_blurry, "Reject blurry photos"),
                          (self.rej_extras, "Reject the weaker frames of each burst / group"),
                          (self.rej_rest, "Reject everything that isn't picked"),
                          (self.respect, "Keep decisions I made by hand")):
            ttk.Checkbutton(c.body, text=text, variable=var, style="Card.TCheckbutton",
                            command=self.update_preview).pack(anchor="w")
        r = tk.Frame(c.body, bg=T.SURF)
        r.pack(fill="x", pady=(px(6), 0))
        tk.Label(r, text="Give picks a star rating", bg=T.SURF, fg=T.TEXT).pack(side="left")
        ttk.Spinbox(r, from_=0, to=5, width=3, textvariable=self.pick_stars, command=self.update_preview).pack(side="right")

        # --- preview / apply
        c = T.Card(right, "Result preview")
        c.pack(fill="x")
        self.big = tk.Frame(c.body, bg=T.SURF)
        self.big.pack(fill="x")
        self.res_picks = self._stat(self.big, "picks", T.PICK)
        self.res_rejects = self._stat(self.big, "rejects", T.REJECT)
        self.res_none = self._stat(self.big, "unflagged", T.MUTE)
        self.res_note = tk.Label(c.body, text="", bg=T.SURF, fg=T.MUTE, font=T.font("small"), justify="left",
                                 anchor="w", wraplength=px(330))
        self.res_note.pack(fill="x", pady=(px(8), 0))
        ttk.Button(c.body, text="Apply suggestions", style="Accent.TButton", command=self.apply).pack(
            fill="x", pady=(px(12), px(4)))
        ttk.Button(c.body, text="Clear all flags", style="Danger.TButton", command=self.clear_flags).pack(fill="x")

        c = T.Card(right, "Tip", "After applying, open Cull and filter by Picks or Rejects to review. Use Ctrl+Z "
                                 "to undo the whole batch.")
        c.pack(fill="x", pady=(px(12), 0))
        self._plan = None
        self._job = None
        self._loading = False

    def _stat(self, parent, label, color):
        f = tk.Frame(parent, bg=T.SURF)
        f.pack(side="left", expand=True, fill="x")
        v = tk.Label(f, text="–", bg=T.SURF, fg=color, font=T.font("big"))
        v.pack()
        tk.Label(f, text=label, bg=T.SURF, fg=T.MUTE, font=T.font("small")).pack()
        return v

    def on_show(self) -> None:
        names = list(presets.load_all_presets().keys())
        self.preset_cb.configure(values=names)
        lib = self.app.lib
        self._loading = True
        if lib:
            s = lib.settings
            self.sharp_w.set(s.sharpness_weight)
            self.blur_thr.set(s.blur_threshold)
            self.profile_cb.set(FOCUS_PROFILES.get(s.focus_profile, "Balanced"))
        else:
            self.profile_cb.set("Balanced")
        self._loading = False
        self._refresh_labels()
        self.update_preview()

    def apply_preset(self) -> None:
        p = presets.get_preset(self.preset_var.get())
        if not p:
            return
        self._loading = True
        self.preset_desc.configure(text=p.description)
        self.strategy_var.set(p.strategy)
        self.minq_var.set(p.threshold)
        self.percent_var.set(p.percentage)
        total = max(0.001, p.sharpness_weight + p.exposure_weight)
        self.sharp_w.set(100 * p.sharpness_weight / total)
        self.blur_thr.set(p.blur_threshold)
        self.profile_cb.set(FOCUS_PROFILES.get(p.focus_profile, "Balanced"))
        self._loading = False
        self._changed(rescore=True)

    def save_preset(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("Save preset", "Preset name:", parent=self)
        if not name:
            return
        prof = next((k for k, v in FOCUS_PROFILES.items() if v == self.profile_cb.get()), "balanced")
        sw = int(round(self.sharp_w.get()))
        presets.save_custom_preset(presets.CullingPreset(
            name=name, description="Custom preset", sharpness_weight=sw, exposure_weight=100 - sw,
            focus_profile=prof, strategy=self.strategy_var.get(), threshold=float(self.minq_var.get()),
            percentage=float(self.percent_var.get()), blur_threshold=float(self.blur_thr.get()), is_builtin=False))
        self.preset_cb.configure(values=list(presets.load_all_presets().keys()))
        self.preset_var.set(name)
        self.app.toast(f"Saved preset '{name}'.", "ok")

    def _refresh_labels(self) -> None:
        sw = self.sharp_w.get()
        self.weight_label.configure(text=f"{sw:.0f}%  /  {100 - sw:.0f}%")
        self.blur_label.configure(text=f"{self.blur_thr.get():.0f}")
        self.minq_label.configure(text=f"{self.minq_var.get():.0f}")

    def _changed(self, rescore: bool = False) -> None:
        self._refresh_labels()
        if self._loading:
            return
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(180, lambda: self._do_change(rescore))

    def _do_change(self, rescore: bool) -> None:
        self._job = None
        lib = self.app.lib
        if lib and rescore:
            s = lib.settings
            s.sharpness_weight = float(self.sharp_w.get())
            s.exposure_weight = 100.0 - s.sharpness_weight
            s.blur_threshold = float(self.blur_thr.get())
            s.focus_profile = next((k for k, v in FOCUS_PROFILES.items() if v == self.profile_cb.get()), "balanced")
            lib.recompute(regroup=False)
            for g in lib.groups:  # best frame depends on scores
                members = lib.group_members(g.id)
                if members and not any(m.best_override for m in members):
                    from ..core.grouping import GroupInput, best_in_group

                    items = [GroupInput(key=m.key, name=m.name, sharpness=m.composite_sharpness,
                                        exposure=m.exposure_score, quality=m.quality_score) for m in members]
                    bi = best_in_group(items, list(range(len(items))))
                    g.best = items[bi].key
                    for m in members:
                        m.is_best = m.key == g.best
            lib.mark_dirty()
            self.app.refresh_counts()
        self.update_preview()

    def options(self) -> selection.CullOptions:
        def safe(var, default):
            try:
                return var.get()
            except (tk.TclError, ValueError):
                return default

        return selection.CullOptions(
            strategy=self.strategy_var.get(), count=int(safe(self.count_var, 50)),
            percent=float(safe(self.percent_var, 20.0)), min_quality=float(self.minq_var.get()),
            max_per_group=int(safe(self.maxgroup_var, 1)), reject_blurry=self.rej_blurry.get(),
            reject_group_extras=self.rej_extras.get(), reject_unpicked=self.rej_rest.get(),
            respect_manual=self.respect.get(), pick_rating=int(safe(self.pick_stars, 0)),
        )

    def update_preview(self) -> None:
        lib = self.app.lib
        if not lib or not lib.analyzed_count():
            for w in (self.res_picks, self.res_rejects, self.res_none):
                w.configure(text="–")
            self.res_note.configure(text="Open and analyse a folder to see suggestions.")
            self._plan = None
            return
        plan = selection.plan_auto_cull(lib, self.options())
        self._plan = plan
        kept = sum(1 for p in lib.photos if p.touched) if self.respect.get() else 0
        T.count_up(self.res_picks, plan.picks + (sum(1 for p in lib.photos if p.touched and p.flag == FLAG_PICK) if kept else 0), duration_ms=350)
        T.count_up(self.res_rejects, plan.rejects + (sum(1 for p in lib.photos if p.touched and p.flag == FLAG_REJECT) if kept else 0), duration_ms=350)
        T.count_up(self.res_none, (len(lib.photos) - plan.picks - plan.rejects -
                                         (sum(1 for p in lib.photos if p.touched and p.flag) if kept else 0)))
        blurry = sum(1 for p in lib.photos if p.blur and p.blur.label == "Blurry")
        note = f"{blurry} photos are clearly blurry at this threshold. {len(lib.groups)} bursts/groups found."
        if kept:
            note += f"\n{kept} hand-made decisions will be kept."
        self.res_note.configure(text=note)

    def apply(self) -> None:
        lib = self.app.lib
        if not lib or self._plan is None:
            return
        self.update_preview()
        self.app.push_undo([lib.by_key[k] for k in self._plan.flags if k in lib.by_key])
        selection.apply_plan(lib, self._plan)
        self.app.refresh_all()
        self.app.toast_undo(f"Applied: {self._plan.picks} picks, {self._plan.rejects} rejects. Review them in Cull.")

    def clear_flags(self) -> None:
        lib = self.app.lib
        if not lib:
            return
        if not messagebox.askyesno("Clear flags", "Remove every pick/reject flag in this folder?\n"
                                   "(Star ratings and colour labels are kept.)", parent=self):
            return
        self.app.push_undo(lib.photos)
        for p in lib.photos:
            p.flag = None
            p.touched = bool(p.rating or p.label)
        lib.mark_dirty()
        self.app.refresh_all()
        self.update_preview()
