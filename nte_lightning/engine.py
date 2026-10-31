"""Capture loop: grabs frames, runs the detectors and fires the shutter."""

from __future__ import annotations

import copy
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .capture import Capture, open_capture
from .config import ALL_REGIONS_WINDOW_S, AppConfig, Region
from .detector import RegionDetector
from .winput import (
    DIRECT_METHOD,
    InputSender,
    PressResult,
    blocked_by_admin,
    foreground_app,
    game_focus,
    game_in_front,
    game_visible,
    high_resolution_timer,
    play_sound,
)

PEAK_HOLD_S = 3.0
FOCUS_CHECK_S = 0.1  # how often to check that the game is in front


@dataclass
class Stats:
    running: bool = False
    armed: bool = False
    backend: str = ""
    fast_backend: bool = True
    capture_size: tuple[int, int] = (0, 0)
    fps: float = 0.0
    process_ms: float = 0.0
    frame_age_ms: float | None = None
    scores: list[float] = field(default_factory=list)
    peaks: list[float] = field(default_factory=list)
    flashes: list[float] = field(default_factory=list)
    warming_up: bool = False
    paused: str = ""  # why detection is paused (game not in front), if it is
    strikes: int = 0
    photos_taken: int = 0
    cooldown_left: float = 0.0


@dataclass
class Event:
    kind: str  # info | warn | error | strike | armed | disarmed | stopped | done | screenshot
    message: str = ""
    data: object = None


class Engine:
    def __init__(
        self,
        config: AppConfig,
        capture_factory: Callable[[int], Capture] = open_capture,
        sender: InputSender | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sound: Callable[[str], None] = play_sound,
        focus_check: Callable[[str], tuple[bool, str]] = game_in_front,
        visible_check: Callable[[str], tuple[bool, str]] = game_visible,
    ):
        self.events: "queue.Queue[Event]" = queue.Queue()
        self._capture_factory = capture_factory
        self._sender = sender or InputSender()
        self._clock = clock
        self._sound = sound
        self._focus_check = focus_check
        self._visible_check = visible_check
        self._lock = threading.Lock()
        self._cfg = copy.deepcopy(config).clamp()
        self._dirty = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats = Stats()
        self._armed = False
        self._screenshot_wanted = False
        self._cooldown_until = 0.0
        self._photo_thread: threading.Thread | None = None
        self._save_queue: "queue.Queue[tuple[np.ndarray, Path]]" = queue.Queue(maxsize=4)
        self._saver: threading.Thread | None = None

    # ---------------------------------------------------------------- control

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def armed(self) -> bool:
        return self._armed

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._dirty = True
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.set_armed(False)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def set_armed(self, armed: bool) -> None:
        if armed == self._armed:
            return
        self._armed = armed
        if armed:
            with self._lock:
                self._stats.photos_taken = 0
                self._stats.strikes = 0
            self._cooldown_until = 0.0
            self.events.put(Event("armed"))
            if self._cfg.sound:
                self._sound("arm")
        else:
            self.events.put(Event("disarmed"))
            if self._cfg.sound:
                self._sound("disarm")

    def apply_config(self, config: AppConfig) -> None:
        with self._lock:
            self._cfg = copy.deepcopy(config).clamp()
            self._dirty = True

    def request_screenshot(self) -> None:
        """Ask the running loop to post the next full frame as an event."""
        self._screenshot_wanted = True

    def test_shutter(self, delay_s: float = 3.0) -> None:
        """Press the photo key once after ``delay_s`` (time to switch to the
        game) and report what happened, without waiting for lightning."""
        cfg = self._cfg

        def run() -> None:
            time.sleep(delay_s)
            app = foreground_app()
            where = app or "this app or the desktop"
            warning = blocked_by_admin()
            if warning:
                self.events.put(Event("warn", warning))
            with game_focus(self._switch_to(cfg)) as problem:
                if problem:
                    self.events.put(Event("warn", f"Test: {problem}"))
                result = self._sender.press(cfg.shutter_key, cfg.random_hold_ms(), cfg.input_method, cfg.game_exe)
            if result.ok:
                self.events.put(
                    Event("info", f"Test: pressed {cfg.shutter_key} ({cfg.input_method}) in {where}. Did the game take a photo?")
                )
            else:
                self.events.put(Event("warn", f"Test: {cfg.shutter_key} not pressed: {result.message}"))

        threading.Thread(target=run, name="test-shutter", daemon=True).start()

    def snapshot(self) -> Stats:
        with self._lock:
            s = copy.copy(self._stats)
            s.scores = list(s.scores)
            s.peaks = list(s.peaks)
            s.flashes = list(s.flashes)
        s.running = self.running
        s.armed = self._armed
        s.cooldown_left = max(0.0, self._cooldown_until - self._clock())
        return s

    # ------------------------------------------------------------------- loop

    def _run(self) -> None:
        try:
            cap = self._capture_factory(self._cfg.monitor)
        except Exception as exc:
            self.events.put(Event("error", f"Could not start screen capture: {exc}"))
            self.events.put(Event("stopped"))
            return
        with self._lock:
            self._stats.backend = cap.name
            self._stats.fast_backend = cap.fast
            self._stats.capture_size = (cap.width, cap.height)
        if not cap.fast:
            self.events.put(Event("warn", f"Using the slower {cap.name} capture; very short strikes may be missed."))
        high_resolution_timer(True)
        try:
            self._loop(cap)
        except Exception as exc:
            self.events.put(Event("error", f"Capture stopped: {exc!r}"))
        finally:
            high_resolution_timer(False)
            cap.close()
            self._armed = False
            self.events.put(Event("stopped"))

    def _rebuild(self, cap: Capture) -> tuple[list[Region], list[RegionDetector], tuple[int, int, int, int] | None]:
        with self._lock:
            cfg = self._cfg
            self._dirty = False
        regions = cfg.regions_for_size(cap.width, cap.height)
        detectors = [RegionDetector(r.w, r.h, cfg.detection, display_height=cap.height) for r in regions]
        box = None
        if regions:
            box = (
                min(r.x for r in regions),
                min(r.y for r in regions),
                max(r.x + r.w for r in regions),
                max(r.y + r.h for r in regions),
            )
        with self._lock:
            n = len(regions)
            self._stats.scores = [0.0] * n
            self._stats.peaks = [0.0] * n
            self._stats.flashes = [0.0] * n
        return regions, detectors, box

    def _loop(self, cap: Capture) -> None:
        regions, detectors, box = self._rebuild(cap)
        peak_t = [0.0] * len(regions)
        last_hit = [-math.inf] * len(regions)
        frames = 0
        fps_t0 = self._clock()
        proc_avg = 0.0
        paused, focus_t = "", -1.0

        while not self._stop.is_set():
            if self._dirty:
                regions, detectors, box = self._rebuild(cap)
                peak_t = [0.0] * len(regions)
                last_hit = [-math.inf] * len(regions)  # when each region last saw lightning
            cfg = self._cfg

            now = time.perf_counter()
            if now - focus_t >= FOCUS_CHECK_S:
                focus_t = now
                if not cfg.game_in_front_only:
                    ok, reason = True, ""
                elif cfg.background_photos:
                    ok, reason = self._visible_check(cfg.game_exe)
                else:
                    ok, reason = self._focus_check(cfg.game_exe)
                was, paused = paused, "" if ok else reason or "the game is not in front"
                if paused and not was:
                    self.events.put(Event("info", f"Paused: {paused}. Watching resumes when the game is back."))
                    for det in detectors:
                        det.reset()  # warm up again on resume
                    with self._lock:
                        self._stats.scores = [0.0] * len(regions)
                elif was and not paused:
                    self.events.put(Event("info", "Game is in front again: watching."))
                with self._lock:
                    self._stats.paused = paused
            if paused and not self._screenshot_wanted:
                time.sleep(0.01)
                continue

            # With DXGI the whole screen costs no more than a part of it, and
            # lets sun beams be traced back to a sun outside the regions.
            full = cap.fast or self._screenshot_wanted or cfg.saving_frames or box is None
            with cap.lock:
                frame = cap.grab(None if full else box)
            if frame is None:
                time.sleep(0.0005)
                continue
            t0 = self._clock()

            if self._screenshot_wanted:
                self._screenshot_wanted = False
                self.events.put(Event("screenshot", data=frame.copy()))
            if paused:
                continue

            ox, oy = (0, 0) if full else (box[0], box[1])
            scores, flashes, hit_index, hit_patch, warming = [], [], -1, None, False
            for i, (r, det) in enumerate(zip(regions, detectors)):
                px = frame[r.y - oy : r.y - oy + r.h, r.x - ox : r.x - ox + r.w]
                res = det.update(px, t0, surround=(frame, r.x - ox, r.y - oy))
                scores.append(res.score)
                flashes.append(res.flash)
                warming = warming or res.warming_up
                if res.triggered:
                    last_hit[i] = t0
                    if hit_index < 0:
                        hit_index = i
                        # Patch that triggered, in display coordinates.
                        hit_patch = (r.x + res.patch[0], r.y + res.patch[1], res.patch[2], res.patch[3])
            if cfg.trigger_mode == "all" and any(t0 - t > ALL_REGIONS_WINDOW_S for t in last_hit):
                hit_index = -1  # not seen in every region yet

            now = self._clock()
            proc_ms = (now - t0) * 1000.0
            proc_avg = proc_ms if proc_avg == 0 else proc_avg * 0.95 + proc_ms * 0.05

            if hit_index >= 0 and now >= self._cooldown_until and not self._photo_pending():
                marks = (regions[hit_index], hit_patch, (ox, oy))
                self._on_strike(cfg, hit_index, scores[hit_index], frame, marks, now, proc_ms, cap.frame_age_ms())

            frames += 1
            with self._lock:
                st = self._stats
                for i, sc in enumerate(scores):
                    if i < len(st.peaks) and (sc >= st.peaks[i] or now - peak_t[i] > PEAK_HOLD_S):
                        st.peaks[i] = sc
                        peak_t[i] = now
                st.scores = scores
                st.flashes = flashes
                st.warming_up = warming
                st.process_ms = proc_avg
                if now - fps_t0 >= 0.5:
                    st.fps = frames / (now - fps_t0)
                    st.frame_age_ms = cap.frame_age_ms()
                    frames, fps_t0 = 0, now

    # --------------------------------------------------------------- strikes

    def _photo_pending(self) -> bool:
        return self._photo_thread is not None and self._photo_thread.is_alive()

    def _on_strike(
        self,
        cfg: AppConfig,
        region_index: int,
        score: float,
        frame: np.ndarray,
        marks: tuple,
        now: float,
        proc_ms: float,
        frame_age_ms: float | None,
    ) -> None:
        with self._lock:
            self._stats.strikes += 1
        timing = f"detected in {proc_ms:.1f} ms"
        if frame_age_ms is not None:
            timing += f", {frame_age_ms:.1f} ms after the frame was shown"
        where = f"region {region_index + 1} (score {score:.1f}, {timing})"

        if self._armed:
            self.events.put(Event("strike", f"Lightning in {where}: taking a photo"))
            self._photo_thread = threading.Thread(target=self._take_photo, args=(cfg,), daemon=True)
            self._photo_thread.start()
            press_s = (cfg.first_press_delay_ms + cfg.key_hold_max_ms) / 1000.0
            self._cooldown_until = now + press_s + cfg.cooldown_s
        else:
            self.events.put(Event("strike", f"[test] Lightning in {where} (not armed, no photo taken)"))
            self._cooldown_until = now + cfg.cooldown_s

        if cfg.sound:
            self._sound("strike")
        if cfg.saving_frames:
            self._queue_save(frame, marks, Path(cfg.save_dir))

    @staticmethod
    def _switch_to(cfg: AppConfig) -> str:
        """The game to switch to for photos, or "" to press in whatever has focus.

        Window messages (Windows) and "Send to game only" (macOS) go straight
        to the game and need no focus.
        """
        if cfg.background_photos and cfg.input_method != DIRECT_METHOD:
            return cfg.game_exe
        return ""

    def _take_photo(self, cfg: AppConfig) -> None:
        if cfg.first_press_delay_ms:
            time.sleep(cfg.first_press_delay_ms / 1000.0)
        if not self._armed:
            return
        with game_focus(self._switch_to(cfg)) as problem:
            if problem:
                self.events.put(Event("warn", f"Photo not taken: {problem}"))
                return
            result: PressResult = self._sender.press(cfg.shutter_key, cfg.random_hold_ms(), cfg.input_method, cfg.game_exe)
        if not result.ok:
            self.events.put(Event("warn", f"Photo not taken: {result.message}"))
            return
        with self._lock:
            self._stats.photos_taken += 1
            taken = self._stats.photos_taken
        if cfg.max_photos and taken >= cfg.max_photos:
            self.events.put(Event("done", f"Took {taken} photo(s), the limit you set. Disarmed."))
            self._armed = False
            self.events.put(Event("disarmed"))
            if cfg.sound:
                self._sound("done")

    # ---------------------------------------------------------------- saving

    def _queue_save(self, frame: np.ndarray, marks: tuple, folder: Path) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        try:
            self._save_queue.put_nowait((frame.copy(), marks, folder / f"lightning_{stamp}"))
        except queue.Full:
            self.events.put(Event("warn", "Skipped saving a detected frame (still saving earlier ones)"))
            return
        if self._saver is None or not self._saver.is_alive():
            self._saver = threading.Thread(target=self._save_worker, daemon=True)
            self._saver.start()

    def _save_worker(self) -> None:
        while True:
            try:
                frame, marks, base = self._save_queue.get(timeout=5.0)
            except queue.Empty:
                return
            try:
                base.parent.mkdir(parents=True, exist_ok=True)
                raw, marked = save_detection(frame, marks, base)
                self.events.put(Event("info", f"Saved detected frame: {raw.name} (and {marked.name} with the spot boxed)"))
            except PermissionError as exc:
                if sys.platform == "darwin":
                    hint = (
                        "macOS may be blocking the folder (Desktop, Documents and Downloads need permission); "
                        "pick another folder or allow this app in System Settings > Privacy & Security > Files and Folders."
                    )
                else:
                    hint = (
                        "Windows may be blocking the folder (Controlled folder access protects Pictures and Documents); "
                        "pick another folder or allow this app in Windows Security."
                    )
                self.events.put(Event("warn", f"Could not save frame: {exc}. {hint}"))
            except Exception as exc:
                self.events.put(Event("warn", f"Could not save frame: {exc}"))


def save_detection(frame: np.ndarray, marks: tuple, base: Path) -> tuple[Path, Path]:
    """Save the frame as PNG, plus a JPEG with the region and triggering spot boxed.

    ``marks`` is (region, patch, origin): the region and the patch (x, y, w, h)
    in display coordinates, and the display position of the frame's corner.
    """
    from PIL import Image, ImageDraw

    region, patch, (ox, oy) = marks
    image = Image.fromarray(np.ascontiguousarray(frame[..., 2::-1]))
    raw = base.with_suffix(".png")
    image.save(raw, compress_level=1)

    draw = ImageDraw.Draw(image)
    width = max(2, image.height // 360)
    draw.rectangle((region.x - ox, region.y - oy, region.x - ox + region.w, region.y - oy + region.h), outline=(51, 224, 255), width=width)
    if patch:
        x, y, w, h = patch
        pad = max(w, h) // 2
        draw.rectangle((x - ox - pad, y - oy - pad, x - ox + w + pad, y - oy + h + pad), outline=(255, 40, 40), width=width * 2)
    marked = base.parent / (base.name + "_marked.jpg")
    image.save(marked, quality=88)
    return raw, marked
