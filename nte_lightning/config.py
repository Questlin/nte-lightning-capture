"""Settings model and JSON persistence."""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .detector import DetectorSettings
from .winput import INPUT_METHODS

TRIGGER_MODES = ["any", "all"]
# In "all" mode, how close together in time the regions must see lightning.
ALL_REGIONS_WINDOW_S = 0.3

APP_NAME = "NTE Lightning Capture"


@dataclass
class Region:
    """A detection rectangle in pixels of the captured display."""

    x: int
    y: int
    w: int
    h: int

    def scaled(self, sx: float, sy: float) -> "Region":
        return Region(round(self.x * sx), round(self.y * sy), max(1, round(self.w * sx)), max(1, round(self.h * sy)))

    def clipped(self, width: int, height: int) -> "Region | None":
        x0, y0 = max(0, self.x), max(0, self.y)
        x1, y1 = min(width, self.x + self.w), min(height, self.y + self.h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return Region(x0, y0, x1 - x0, y1 - y0)


def default_save_dir() -> str:
    return str(Path.home() / "Pictures" / APP_NAME)


@dataclass
class AppConfig:
    monitor: int = 0
    regions: list[Region] = field(default_factory=list)
    # Display size the regions were drawn on, used to rescale them if the
    # resolution changes later.
    regions_ref_size: tuple[int, int] = (0, 0)

    # Each strike takes one photo.
    max_photos: int = 1  # 0 = no limit
    first_press_delay_ms: int = 0
    cooldown_s: float = 2.0

    shutter_key: str = "F"
    # Each press holds the key for a random time in this range (ms).
    key_hold_min_ms: int = 50
    key_hold_max_ms: int = 100
    # How the key press is sent (see winput.INPUT_METHODS); games differ in
    # which they accept.
    input_method: str = INPUT_METHODS[0]
    arm_hotkey: str = "F6"

    detection: DetectorSettings = field(default_factory=DetectorSettings)

    # "any": lightning in any one region triggers; "all": it must show up in
    # every region (within ALL_REGIONS_WINDOW_S of each other).
    trigger_mode: str = TRIGGER_MODES[0]

    # Shows the advanced settings, live signal and log, and enables saving
    # detected frames.
    debug: bool = False
    save_frames: bool = False  # only while debug is on
    save_dir: str = field(default_factory=default_save_dir)
    sound: bool = True
    # Only look for lightning while the game's window has focus, so other
    # windows and menus popping up don't count.
    game_in_front_only: bool = True
    # The game's program file name, learned when the arm hotkey is pressed
    # in it or regions are selected over it. Empty: any other app counts.
    game_exe: str = ""
    # Also take photos while the game is visible but another window has
    # focus: the app switches to the game for the photo, then back.
    background_photos: bool = False

    def clamp(self) -> "AppConfig":
        self.monitor = max(0, int(self.monitor))
        self.max_photos = max(0, int(self.max_photos))
        self.first_press_delay_ms = min(5_000, max(0, int(self.first_press_delay_ms)))
        self.cooldown_s = min(120.0, max(0.0, float(self.cooldown_s)))
        self.key_hold_min_ms = min(500, max(1, int(self.key_hold_min_ms)))
        self.key_hold_max_ms = min(500, max(self.key_hold_min_ms, int(self.key_hold_max_ms)))
        if self.trigger_mode not in TRIGGER_MODES:
            self.trigger_mode = TRIGGER_MODES[0]
        if self.input_method not in INPUT_METHODS:
            self.input_method = INPUT_METHODS[0]
        self.detection.clamp()
        return self

    @property
    def saving_frames(self) -> bool:
        return self.save_frames and self.debug

    def random_hold_ms(self) -> int:
        return random.randint(self.key_hold_min_ms, self.key_hold_max_ms)

    def regions_for_size(self, width: int, height: int) -> list[Region]:
        """Regions rescaled to the given display size and clipped to it."""
        rw, rh = self.regions_ref_size
        sx = width / rw if rw > 0 else 1.0
        sy = height / rh if rh > 0 else 1.0
        out = []
        for r in self.regions:
            if (sx, sy) != (1.0, 1.0):
                r = r.scaled(sx, sy)
            r = r.clipped(width, height)
            if r is not None:
                out.append(r)
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        cfg = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "regions":
                value = [Region(**{k: int(r[k]) for k in ("x", "y", "w", "h")}) for r in value]
            elif key == "regions_ref_size":
                value = (int(value[0]), int(value[1]))
            elif key == "detection":
                det = DetectorSettings()
                det_known = {f.name for f in fields(DetectorSettings)}
                for k, v in value.items():
                    if k in det_known:
                        setattr(det, k, type(getattr(det, k))(v))
                value = det
            else:
                default = getattr(cfg, key)
                value = type(default)(value)
            setattr(cfg, key, value)
        return cfg.clamp()


def config_path() -> Path:
    if sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "NTELightningCapture" / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    try:
        return AppConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return AppConfig()
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        # A broken file should never stop the app from starting.
        return AppConfig()


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
