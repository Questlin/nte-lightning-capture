"""Main window."""

from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import __version__
from .capture import list_monitors, open_capture
from .config import APP_NAME, AppConfig, Region, load_config, save_config
from .detector import DetectorSettings
from .engine import Engine, Event
from .region_picker import RegionPicker
from .winput import (
    INPUT_METHODS,
    IS_MAC,
    IS_WINDOWS,
    KeyWatcher,
    all_input_names,
    blocked_by_admin,
    enable_dpi_awareness,
    foreground_app,
    hide_app,
    is_valid_input,
    name_from_tk_event,
    RELAUNCHED_FLAG,
    permission_problems,
    process_elevated,
    relaunch_as_admin,
    show_app,
)

POLL_MS = 33
# How the trigger modes read in the UI.
MODE_LABELS = {"any": "any region", "all": "all regions"}
HOTKEY_POLL_MS = 15

COLORS = {
    "stopped": "#7a7a7a",
    "watching": "#2f7fd6",
    "armed": "#d63a2f",
    "paused": "#b07a1e",
    "hot": "#e8442e",
    "bar": "#3d9be9",
    "peak": "#f2b233",
    "track": "#e4e4e4",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg: AppConfig = load_config()
        self.engine = Engine(self.cfg)
        self.monitors = list_monitors()
        self.preview_image: Image.Image | None = None
        self._preview_photo = None
        self._save_job: str | None = None
        self._picker_pending = False
        self._hotkey = KeyWatcher()
        self._meter_rows = -1
        self._screenshot_queue: "queue.Queue[object]" = queue.Queue()

        root.title(f"{APP_NAME} {__version__}")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.ui_scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.elevated = bool(process_elevated())

        self._build_vars()
        self._build_ui()
        self._refresh_regions()
        self._update_state_label()
        self.log(f"Ready. Shutter: {self.cfg.shutter_key}. Arm/disarm hotkey: {self.cfg.arm_hotkey}.")
        if IS_MAC:
            for line in permission_problems():
                self.log(line)
            if self.cfg.arm_hotkey.startswith("F") and len(self.cfg.arm_hotkey) > 1:
                self.log(
                    f"On a Mac keyboard, press fn+{self.cfg.arm_hotkey} unless the F keys are set to work as standard "
                    "function keys (System Settings > Keyboard)."
                )
        elif not IS_WINDOWS:
            self.log("Note: not running on Windows or macOS, so photos cannot be triggered here.")
        elif not self.elevated:
            self.log("Not running as administrator, so if the game is, Windows will block the photo key.")
        root.after(POLL_MS, self._poll)
        root.after(HOTKEY_POLL_MS, self._poll_hotkey)

    # ------------------------------------------------------------------ vars

    def _build_vars(self) -> None:
        c, d = self.cfg, self.cfg.detection
        self.v_max = tk.IntVar(value=c.max_photos)
        self.v_delay = tk.IntVar(value=c.first_press_delay_ms)
        self.v_cooldown = tk.DoubleVar(value=c.cooldown_s)
        self.v_shutter = tk.StringVar(value=c.shutter_key)
        self.v_hold_min = tk.IntVar(value=c.key_hold_min_ms)
        self.v_hold_max = tk.IntVar(value=c.key_hold_max_ms)
        self.v_mode = tk.StringVar(value=MODE_LABELS[c.trigger_mode])
        self.v_debug = tk.BooleanVar(value=c.debug)
        self.v_method = tk.StringVar(value=c.input_method)
        self.v_hotkey = tk.StringVar(value=c.arm_hotkey)
        self.v_jump = tk.IntVar(value=d.jump)
        self.v_minb = tk.IntVar(value=d.min_brightness)
        self.v_area = tk.DoubleVar(value=d.trigger_pct)
        self.v_flash = tk.DoubleVar(value=d.flash_jump)
        self.v_save = tk.BooleanVar(value=c.save_frames)
        self.v_savedir = tk.StringVar(value=c.save_dir)
        self.v_sound = tk.BooleanVar(value=c.sound)
        self.v_front = tk.BooleanVar(value=c.game_in_front_only)
        self.v_background = tk.BooleanVar(value=c.background_photos)
        self.v_rays = tk.BooleanVar(value=d.ignore_sun_rays)
        self.v_busy = tk.BooleanVar(value=d.busy_margin > 0)
        labels = [m.label for m in self.monitors] or ["Display 1"]
        self.v_monitor = tk.StringVar(value=labels[min(c.monitor, len(labels) - 1)])
        for var in (
            self.v_max, self.v_delay, self.v_cooldown, self.v_shutter, self.v_method,
            self.v_hold_min, self.v_hold_max, self.v_mode, self.v_hotkey, self.v_jump, self.v_minb, self.v_area, self.v_flash,
            self.v_save, self.v_savedir, self.v_sound, self.v_front, self.v_background, self.v_rays, self.v_busy,
        ):
            var.trace_add("write", lambda *_: self._on_form_change())
        self.v_monitor.trace_add("write", lambda *_: self._on_monitor_change())
        self.v_debug.trace_add("write", lambda *_: self._on_debug_change())

    def _read_form(self) -> None:
        """Copy valid form values into the config; ignore half-typed ones."""
        c, d = self.cfg, self.cfg.detection

        def get(var, current):
            try:
                return var.get()
            except (tk.TclError, ValueError):
                return current

        c.max_photos = get(self.v_max, c.max_photos)
        c.first_press_delay_ms = get(self.v_delay, c.first_press_delay_ms)
        c.cooldown_s = get(self.v_cooldown, c.cooldown_s)
        c.key_hold_min_ms = get(self.v_hold_min, c.key_hold_min_ms)
        c.key_hold_max_ms = get(self.v_hold_max, c.key_hold_max_ms)
        c.trigger_mode = {label: mode for mode, label in MODE_LABELS.items()}.get(self.v_mode.get(), c.trigger_mode)
        c.input_method = self.v_method.get()
        if is_valid_input(self.v_shutter.get()):
            c.shutter_key = self.v_shutter.get()
        if is_valid_input(self.v_hotkey.get()):
            c.arm_hotkey = self.v_hotkey.get()
        d.jump = get(self.v_jump, d.jump)
        d.min_brightness = get(self.v_minb, d.min_brightness)
        d.trigger_pct = get(self.v_area, d.trigger_pct)
        d.flash_jump = get(self.v_flash, d.flash_jump)
        c.save_frames = self.v_save.get()
        c.save_dir = self.v_savedir.get()
        c.sound = self.v_sound.get()
        c.game_in_front_only = self.v_front.get()
        c.background_photos = self.v_background.get()
        d.ignore_sun_rays = self.v_rays.get()
        if self.v_busy.get() != (d.busy_margin > 0):
            d.busy_margin = DetectorSettings().busy_margin if self.v_busy.get() else 0.0
        c.clamp()

    def _on_form_change(self) -> None:
        self._read_form()
        self.engine.apply_config(self.cfg)
        self._schedule_save()
        self.hotkey_label.configure(text=self._hotkey_text())
        self.front_check.configure(text=self._front_text())

    def _restore_detection(self) -> None:
        d = DetectorSettings()
        # Each set re-reads the form into the config, so set them all first.
        self.v_area.set(d.trigger_pct)
        self.v_jump.set(d.jump)
        self.v_minb.set(d.min_brightness)
        self.v_flash.set(d.flash_jump)
        self.v_rays.set(d.ignore_sun_rays)
        self.v_busy.set(d.busy_margin > 0)
        self.cfg.detection = d
        self.engine.apply_config(self.cfg)
        self._schedule_save()
        self.log("Detection sensitivity restored to defaults.")

    def _on_debug_change(self) -> None:
        self.cfg.debug = self.v_debug.get()
        self.engine.apply_config(self.cfg)
        self._schedule_save()
        self._apply_debug()

    def _on_monitor_change(self) -> None:
        labels = [m.label for m in self.monitors]
        if self.v_monitor.get() in labels:
            new = self.monitors[labels.index(self.v_monitor.get())].index
            if new != self.cfg.monitor:
                self.cfg.monitor = new
                self.preview_image = None
                self._refresh_regions()
                self._schedule_save()
                if self.engine.running:
                    armed = self.engine.armed
                    self.engine.stop()
                    self.engine.apply_config(self.cfg)
                    self.engine.start()
                    self.engine.set_armed(armed)
                else:
                    self.engine.apply_config(self.cfg)

    def _schedule_save(self) -> None:
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        try:
            save_config(self.cfg)
        except OSError as exc:
            self.log(f"Could not save settings: {exc}")

    # -------------------------------------------------------------------- UI

    def px(self, n: float) -> int:
        return int(n * self.ui_scale)

    def _build_ui(self) -> None:
        root = self.root
        pad = {"padx": self.px(6), "pady": self.px(4)}
        gap = (self.px(8), 0)
        self._debug_only: list[tk.Widget] = []  # shown only in debug mode
        self._compact_only: list[tk.Widget] = []  # shown only outside debug mode
        main = ttk.Frame(root, padding=self.px(8))
        main.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(2, weight=1)

        # --- status / controls -------------------------------------------
        top = ttk.Frame(main)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)
        top.columnconfigure(0, weight=1)
        self.state_label = tk.Label(top, text="", fg="white", font=("Segoe UI", 13, "bold"), padx=10, pady=6, anchor="w")
        self.state_label.grid(row=0, column=0, sticky="ew")
        self.watch_btn = ttk.Button(top, text="Start watching", command=self._toggle_watch)
        self.watch_btn.grid(row=0, column=1, padx=self.px(4))
        self.arm_btn = ttk.Button(top, text="Arm", command=self._toggle_arm)
        self.arm_btn.grid(row=0, column=2)
        self.hotkey_label = ttk.Label(top, text=self._hotkey_text(), foreground="#555")
        self.hotkey_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(self.px(4), 0))
        self.stats_label = ttk.Label(top, text="", foreground="#555")
        self.stats_label.grid(row=2, column=0, columnspan=3, sticky="w")
        self._debug_only.append(self.stats_label)
        # Outside debug mode the log is hidden; its latest line shows here.
        self.last_label = ttk.Label(top, text="", foreground="#555", width=80)  # fixed: long lines don't widen the window
        self.last_label.grid(row=3, column=0, columnspan=3, sticky="w")
        self._compact_only.append(self.last_label)

        # --- left column: regions + meters ----------------------------------
        left = ttk.Frame(main)
        left.grid(row=1, column=0, sticky="nsew", **pad)
        left.columnconfigure(0, weight=1)

        reg = ttk.LabelFrame(left, text="Where to look for lightning", padding=self.px(6))
        reg.grid(row=0, column=0, sticky="ew")
        reg.columnconfigure(0, weight=1)
        self.preview = tk.Canvas(reg, width=self.px(384), height=self.px(216), background="#1d1d1d", highlightthickness=0)
        self.preview.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Button(reg, text="Select regions on screen…", command=self._pick_regions).grid(
            row=1, column=0, sticky="w", pady=(self.px(6), 0)
        )
        ttk.Button(reg, text="Clear", command=self._clear_regions).grid(row=1, column=1, pady=(self.px(6), 0))
        ttk.Label(reg, text="Take photos when lightning is in").grid(row=2, column=0, sticky="w", pady=(self.px(6), 0))
        ttk.Combobox(reg, textvariable=self.v_mode, values=list(MODE_LABELS.values()), state="readonly", width=12).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(self.px(6), 0)
        )
        ttk.Label(reg, text="Display:").grid(row=3, column=0, sticky="w", pady=(self.px(6), 0))
        ttk.Combobox(
            reg, textvariable=self.v_monitor, values=[m.label for m in self.monitors] or ["Display 1"],
            state="readonly", width=30,
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        self.region_label = ttk.Label(reg, text="")
        self.region_label.grid(row=5, column=0, columnspan=3, sticky="w", pady=(self.px(4), 0))

        met = ttk.LabelFrame(left, text="Live signal (sudden brightening per region)", padding=self.px(6))
        met.grid(row=1, column=0, sticky="nsew", pady=gap)
        met.columnconfigure(0, weight=1)
        met.rowconfigure(0, weight=1)
        self.meter = tk.Canvas(met, height=self.px(60), background="white", highlightthickness=0)
        self.meter.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            met, wraplength=self.px(380), foreground="#555",
            text="Start watching and let the scene run. The bar should stay left of the red line "
            "while there is no lightning. If the character, fish or the controls reach it, move the "
            "region away from them or raise 'Trigger level'.",
        ).grid(row=1, column=0, sticky="w", pady=(self.px(4), 0))
        self._debug_only.append(met)

        # --- right column: settings ----------------------------------------
        right = ttk.Frame(main)
        right.grid(row=1, column=1, sticky="nsew", **pad)
        right.columnconfigure(0, weight=1)

        keys = ttk.LabelFrame(right, text="Keys", padding=self.px(6))
        keys.grid(row=0, column=0, sticky="ew")
        self._key_row(keys, 0, "Game's photo key/button", self.v_shutter)
        ttk.Label(keys, text="Send the key as").grid(row=1, column=0, sticky="w", pady=self.px(1))
        ttk.Combobox(keys, textvariable=self.v_method, values=INPUT_METHODS, width=14, state="readonly").grid(
            row=1, column=1, sticky="w", padx=self.px(6)
        )
        ttk.Button(keys, text="Test photo key", command=self._test_shutter).grid(row=1, column=2, sticky="w")
        self._key_row(keys, 2, "Arm/disarm hotkey", self.v_hotkey)
        self._debug_only += self._spin(keys, 3, "Hold key for at least (ms)", self.v_hold_min, 1, 500, 5)
        self._debug_only += self._spin(keys, 4, "… and at most (ms)", self.v_hold_max, 1, 500, 5, "random in between")

        opt = ttk.LabelFrame(right, text="Options", padding=self.px(6))
        opt.grid(row=1, column=0, sticky="ew", pady=gap)
        opt.columnconfigure(1, weight=1)
        ttk.Checkbutton(opt, text="Beep on arm / strike", variable=self.v_sound).grid(row=0, column=0, columnspan=3, sticky="w")
        self.front_check = ttk.Checkbutton(opt, text=self._front_text(), variable=self.v_front)
        self.front_check.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            opt, text="Take photos even when the game isn't focused (switches to it for a moment)", variable=self.v_background
        ).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(opt, text="Debug options", variable=self.v_debug).grid(row=3, column=0, columnspan=3, sticky="w")
        save = ttk.Checkbutton(opt, text="Also save each detected frame (plus a copy with the spot boxed) to:", variable=self.v_save)
        save.grid(row=4, column=0, columnspan=3, sticky="w")
        save_dir = ttk.Entry(opt, textvariable=self.v_savedir)
        save_dir.grid(row=5, column=0, columnspan=2, sticky="ew")
        browse = ttk.Button(opt, text="Browse…", command=self._browse_dir)
        browse.grid(row=5, column=2, padx=(self.px(4), 0))
        self._debug_only += [save, save_dir, browse]

        ph = ttk.LabelFrame(right, text="Photos", padding=self.px(6))
        ph.grid(row=2, column=0, sticky="ew", pady=gap)
        self._spin(ph, 0, "Stop after this many photos", self.v_max, 0, 100000, 1, "0 = keep going until you disarm")
        self._spin(ph, 1, "Wait after a strike (s)", self.v_cooldown, 0, 120, 0.5, "pause before reacting to the next strike")
        self._spin(ph, 2, "Delay before photo (ms)", self.v_delay, 0, 5000, 5)
        self._debug_only.append(ph)

        det = ttk.LabelFrame(right, text="Detection sensitivity", padding=self.px(6))
        det.grid(row=3, column=0, sticky="ew", pady=gap)
        self._spin(det, 0, "Trigger level (red line)", self.v_area, 0.1, 100, 0.5, "lower = more sensitive")
        self._spin(det, 1, "Brightness jump (0-255)", self.v_jump, 1, 255, 5, "how much a pixel must brighten")
        self._spin(det, 2, "Minimum brightness (0-255)", self.v_minb, 0, 255, 5, "how bright a bolt pixel must be")
        self._spin(det, 3, "Sky flash (avg. rise, 0=off)", self.v_flash, 0, 255, 1, "for strikes that light up the sky")
        ttk.Checkbutton(det, text="Ignore sun beams (streaks that run back to the sun)", variable=self.v_rays).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(self.px(4), 0)
        )
        ttk.Checkbutton(det, text="Ask more of spots that keep flickering (waves, UI)", variable=self.v_busy).grid(
            row=5, column=0, columnspan=3, sticky="w"
        )
        ttk.Button(det, text="Restore defaults", command=self._restore_detection).grid(
            row=6, column=0, sticky="w", pady=(self.px(4), 0)
        )
        self._debug_only.append(det)

        # --- log -----------------------------------------------------------
        logf = ttk.LabelFrame(main, text="Log", padding=self.px(6))
        logf.grid(row=2, column=0, columnspan=2, sticky="nsew", **pad)
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log_text = tk.Text(logf, height=7, width=50, state="disabled", wrap="word", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)
        self._debug_only.append(logf)
        self._apply_debug()

    def _apply_debug(self) -> None:
        debug = self.cfg.debug
        for w in self._debug_only:
            w.grid() if debug else w.grid_remove()
        for w in self._compact_only:
            w.grid_remove() if debug else w.grid()
        self.root.geometry("")  # shrink or grow to fit what is shown

    def _spin(self, parent, row, label, var, lo, hi, step, hint=""):
        widgets = [ttk.Label(parent, text=label), ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step, width=8)]
        widgets[0].grid(row=row, column=0, sticky="w", pady=self.px(1))
        widgets[1].grid(row=row, column=1, sticky="w", padx=self.px(6))
        if hint:
            widgets.append(ttk.Label(parent, text=hint, foreground="#777"))
            widgets[2].grid(row=row, column=2, sticky="w")
        return widgets

    def _key_row(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=self.px(1))
        ttk.Combobox(parent, textvariable=var, values=all_input_names(), width=12, state="readonly").grid(
            row=row, column=1, sticky="w", padx=self.px(6)
        )
        ttk.Button(parent, text="Press a key…", command=lambda: self._detect_key(var)).grid(row=row, column=2, sticky="w")

    def _hotkey_text(self) -> str:
        return (
            f"Hotkey {self.cfg.arm_hotkey} arms/disarms from inside the game. "
            f"Photos are sent with '{self.cfg.shutter_key}' to "
            + ("the game, focused or not." if self.cfg.background_photos else "the focused window.")
        )

    # ------------------------------------------------------------- actions

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {message}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.last_label.configure(text=f"{stamp}  {message}")

    def _toggle_watch(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.log("Stopped watching.")
        else:
            if not self.cfg.regions:
                self.log("Tip: select at least one region first.")
            self.engine.apply_config(self.cfg)
            self.engine.start()
            self.log("Watching the screen (test mode: strikes are logged, no photos taken).")
        self._update_state_label()

    def _toggle_arm(self) -> None:
        self._set_armed(not self.engine.armed)

    def _set_armed(self, armed: bool) -> None:
        if armed:
            if not self.cfg.regions:
                messagebox.showwarning(APP_NAME, "Select at least one region to watch first.")
                return
            if not self.engine.running:
                self.engine.apply_config(self.cfg)
                self.engine.start()
        self.engine.set_armed(armed)
        self._update_state_label()

    def _clear_regions(self) -> None:
        self.cfg.regions = []
        self._regions_changed()

    def _regions_changed(self) -> None:
        self.engine.apply_config(self.cfg)
        self._refresh_regions()
        self._schedule_save()

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.v_savedir.get() or None)
        if path:
            self.v_savedir.set(path)

    def _detect_key(self, var: tk.StringVar) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Press a key")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        ttk.Label(dlg, text="Press the key now (Esc to cancel).\nFor mouse buttons pick from the list instead.",
                  padding=self.px(16), justify="center").pack()

        def on_key(e: tk.Event) -> None:
            if e.keysym == "Escape":
                dlg.destroy()
                return
            name = name_from_tk_event(e.keycode, e.keysym)
            if name:
                var.set(name)
                dlg.destroy()

        dlg.bind("<KeyPress>", on_key)
        dlg.grab_set()
        dlg.focus_force()

    # ------------------------------------------------------------- regions

    def _pick_regions(self) -> None:
        if self.engine.armed:
            self._set_armed(False)
            self.log("Disarmed while selecting regions.")
        self.root.withdraw()
        if IS_MAC:
            hide_app()  # hiding the window alone leaves this app in front on macOS
        # With this window hidden, the game is normally the one in front.
        self.root.after(350, self._learn_game)
        if self.engine.running:
            self._picker_pending = True
            self.root.after(400, self.engine.request_screenshot)
            self.root.after(3000, self._picker_timeout)
        else:
            self.root.after(400, self._grab_for_picker)

    def _grab_for_picker(self) -> None:
        frame = None
        try:
            cap = open_capture(self.cfg.monitor)
            try:
                frame = cap.grab_full_blocking()
            finally:
                cap.close()
        except Exception as exc:
            self.log(f"Could not take a screenshot: {exc}")
        self._open_picker(frame)

    def _picker_timeout(self) -> None:
        if self._picker_pending:
            self._picker_pending = False
            self._open_picker(None)

    def _open_picker(self, frame) -> None:
        if IS_MAC:
            show_app()
        if frame is not None:
            image = Image.fromarray(frame[..., 2::-1].copy())
            self.preview_image = image
        else:
            self.log("No screenshot available; drawing on a blank screen.")
            rw, rh = self.cfg.regions_ref_size
            mon = next((m for m in self.monitors if m.index == self.cfg.monitor), None)
            size = (mon.width, mon.height) if mon else ((rw, rh) if rw else (1920, 1080))
            image = Image.new("RGB", size, (30, 30, 30))
        regions = self.cfg.regions_for_size(*image.size)

        def done(result: list[Region] | None) -> None:
            self.root.deiconify()
            if result is not None:
                self.cfg.regions = result
                self.cfg.regions_ref_size = image.size
                self._regions_changed()
                self.log(f"Saved {len(result)} region(s).")
            else:
                self._refresh_regions()

        RegionPicker(self.root, image, regions, done)

    def _refresh_regions(self) -> None:
        n = len(self.cfg.regions)
        self.region_label.configure(
            text=f"{n} region(s) selected." if n else "No regions yet. Open the game, then click 'Select regions'."
        )
        c = self.preview
        c.delete("all")
        cw, ch = int(c["width"]), int(c["height"])
        if self.preview_image is not None:
            iw, ih = self.preview_image.size
            scale = min(cw / iw, ch / ih)
            thumb = self.preview_image.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.BILINEAR)
            self._preview_photo = ImageTk.PhotoImage(thumb)
            c.create_image(0, 0, image=self._preview_photo, anchor="nw")
            regions = self.cfg.regions_for_size(iw, ih)
        else:
            iw, ih = self.cfg.regions_ref_size
            if not iw:
                iw, ih = 1920, 1080
            scale = min(cw / iw, ch / ih)
            c.create_rectangle(0, 0, iw * scale, ih * scale, outline="#555")
            regions = self.cfg.regions
        for i, r in enumerate(regions):
            c.create_rectangle(r.x * scale, r.y * scale, (r.x + r.w) * scale, (r.y + r.h) * scale, outline="#33e0ff", width=2)
            c.create_text(r.x * scale + 4, r.y * scale + 2, text=str(i + 1), anchor="nw", fill="#33e0ff")
        if not regions:
            c.create_text(cw / 2, ch / 2, text="No regions", fill="#aaa")

    # ---------------------------------------------------------------- polling

    def _front_text(self) -> str:
        game = self.cfg.game_exe or "the game"
        if self.cfg.background_photos:
            return f"Only watch while {game} is visible (not minimized or covered)"
        return f"Only watch while {game} has focus"

    def _test_shutter(self) -> None:
        self.log(f"Test: switch to the game now; {self.cfg.shutter_key} will be pressed once in 3 seconds.")
        self.engine.apply_config(self.cfg)
        self.engine.test_shutter(3.0)

    def _learn_game(self) -> None:
        """Remember the app in front as the game (windowed or not)."""
        app = foreground_app()
        if app and app != self.cfg.game_exe:
            self.cfg.game_exe = app
            self.engine.apply_config(self.cfg)
            self._schedule_save()
            self.front_check.configure(text=self._front_text())
            self.log(f"Game: {app}. Watching pauses while another window has focus.")

    def _poll_hotkey(self) -> None:
        try:
            if self._hotkey.pressed(self.cfg.arm_hotkey):
                # With background photos the hotkey may be pressed in another
                # app, so keep a game that is already known.
                if not self.engine.armed and not (self.cfg.background_photos and self.cfg.game_exe):
                    self._learn_game()
                    warning = blocked_by_admin()
                    if warning:
                        self.log(warning)
                self._set_armed(not self.engine.armed)
        finally:
            self.root.after(HOTKEY_POLL_MS, self._poll_hotkey)

    def _poll(self) -> None:
        try:
            while True:
                try:
                    ev: Event = self.engine.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(ev)
            self._draw_meters()
        finally:
            self.root.after(POLL_MS, self._poll)

    def _handle_event(self, ev: Event) -> None:
        if ev.kind == "screenshot":
            if self._picker_pending:
                self._picker_pending = False
                self._open_picker(ev.data)
            return
        if ev.kind == "armed":
            self.log("ARMED: photos will be taken on lightning. Keep the game window focused.")
        elif ev.kind == "disarmed":
            self.log("Disarmed.")
        elif ev.kind == "stopped":
            self.log("Capture stopped.")
        elif ev.message:
            self.log(ev.message)
        self._update_state_label()

    def _update_state_label(self) -> None:
        paused = self.engine.running and self.engine.snapshot().paused
        if paused:
            mode = "armed" if self.engine.armed else "test mode"
            why = "isn't visible" if self.cfg.background_photos else "doesn't have focus"
            text, color = f"Paused ({mode}): the game {why}", COLORS["paused"]
        elif self.engine.armed:
            text, color = "ARMED: taking photos when lightning strikes", COLORS["armed"]
        elif self.engine.running:
            text, color = "Watching (test mode, no photos)", COLORS["watching"]
        else:
            text, color = "Stopped", COLORS["stopped"]
        self.state_label.configure(text=text, bg=color)
        self.watch_btn.configure(text="Stop" if self.engine.running else "Start watching")
        self.arm_btn.configure(text="Disarm" if self.engine.armed else "Arm")

    def _draw_meters(self) -> None:
        st = self.engine.snapshot()
        if st.running:
            age = f" · frame→decision {st.frame_age_ms:.1f} ms" if st.frame_age_ms is not None else ""
            limit = f" / {self.cfg.max_photos}" if self.cfg.max_photos else ""
            cool = f" · next strike in {st.cooldown_left:.1f} s" if st.cooldown_left > 0 else ""
            self.stats_label.configure(
                text=f"{st.backend} · {st.fps:.0f} fps · detection {st.process_ms:.1f} ms/frame{age}"
                f"   |   strikes {st.strikes} · photos {st.photos_taken}{limit}{cool}"
            )
        else:
            self.stats_label.configure(text="")

        if not self.cfg.debug:
            return  # the meters are hidden
        c = self.meter
        n = len(self.cfg.regions)
        row_h = self.px(26)
        if n != self._meter_rows:
            self._meter_rows = n
            c.configure(height=max(self.px(40), n * row_h + self.px(8)))
        c.delete("all")
        width = max(50, c.winfo_width())
        if not n:
            c.create_text(width / 2, self.px(20), text="No regions selected", fill="#888")
            return
        thr = self.cfg.detection.trigger_pct
        label_w = self.px(34)
        text_w = self.px(150)
        bar_w = max(20, width - label_w - text_w - self.px(10))
        full = thr * 3.0  # threshold sits at one third of the bar
        for i in range(n):
            y = self.px(4) + i * row_h
            score = st.scores[i] if st.running and i < len(st.scores) else 0.0
            peak = st.peaks[i] if st.running and i < len(st.peaks) else 0.0
            x0 = label_w
            c.create_text(self.px(4), y + row_h / 2 - 2, text=f"R{i + 1}", anchor="w", font=("Segoe UI", 9, "bold"))
            c.create_rectangle(x0, y + 3, x0 + bar_w, y + row_h - 7, fill=COLORS["track"], outline="")
            fill = COLORS["hot"] if score >= thr else COLORS["bar"]
            c.create_rectangle(x0, y + 3, x0 + bar_w * min(1.0, score / full), y + row_h - 7, fill=fill, outline="")
            px_peak = x0 + bar_w * min(1.0, peak / full)
            c.create_line(px_peak, y + 1, px_peak, y + row_h - 5, fill=COLORS["peak"], width=2)
            tx = x0 + bar_w / 3
            c.create_line(tx, y, tx, y + row_h - 4, fill="#c00000", width=2)
            c.create_text(
                x0 + bar_w + self.px(6), y + row_h / 2 - 2, anchor="w", font=("Segoe UI", 8),
                text=f"{score:4.1f}   peak {peak:4.1f}",
            )

    def _on_close(self) -> None:
        self._read_form()
        self.engine.stop()
        self._save_now()
        self.root.destroy()


def main() -> None:
    # The .exe asks for administrator rights itself (built with --uac-admin):
    # NTE runs as administrator, and Windows blocks key presses into it from
    # apps that don't. When run from source, restart elevated here instead.
    first_copy = RELAUNCHED_FLAG not in sys.argv
    if IS_WINDOWS and first_copy and not process_elevated() and relaunch_as_admin():
        return  # the administrator copy takes over
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if IS_WINDOWS else "aqua" if IS_MAC else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
