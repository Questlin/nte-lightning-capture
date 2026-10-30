import threading
import time

import pytest

from nte_lightning.capture import Capture
from nte_lightning.config import AppConfig, Region
from nte_lightning.engine import Engine
from nte_lightning.winput import PressResult

from .scene import NightScene

FPS = 60.0


class FakeCapture(Capture):
    name = "fake"

    def __init__(self, frames):
        super().__init__()
        self.frames = frames
        self.i = 0
        self.t = 0.0
        self.height, self.width = frames[0].shape[:2]
        self.done = threading.Event()

    def grab(self, box=None):
        if self.i >= len(self.frames):
            self.done.set()
            return None
        frame = self.frames[self.i]
        self.i += 1
        self.t = self.i / FPS
        if box is not None:
            l, t, r, b = box
            frame = frame[t:b, l:r]
        return frame


class FakeSender:
    def __init__(self):
        self.presses = []

    def press(self, name, hold_ms, method=None, game=""):
        self.presses.append(name)
        return PressResult(True)


def make_frames(bolts_at, seconds=3.0):
    scene = NightScene(640, 360)
    n = int(seconds * FPS)
    return [scene.frame(i / FPS, bolt=float(i in bolts_at)) for i in range(n)]


def in_front(_game):
    return True, ""


def run_engine(frames, armed=True, focus_check=in_front, visible_check=in_front, regions=None, **cfg_kwargs):
    regions = regions or [Region(0, 0, 640, 200)]
    cfg = AppConfig(regions=regions, regions_ref_size=(640, 360), sound=False, **cfg_kwargs)
    cfg.key_hold_min_ms = cfg.key_hold_max_ms = 1
    cap = FakeCapture(frames)
    sender = FakeSender()
    engine = Engine(
        cfg,
        capture_factory=lambda _: cap,
        sender=sender,
        clock=lambda: cap.t,
        sound=lambda _: None,
        focus_check=focus_check,
        visible_check=visible_check,
    )
    engine.start()
    engine.set_armed(armed)
    assert cap.done.wait(10)
    time.sleep(0.3)  # let the photo burst finish
    stats = engine.snapshot()
    engine.stop()
    events = []
    while not engine.events.empty():
        events.append(engine.events.get())
    return stats, sender.presses, events


def test_strike_takes_one_photo():
    stats, presses, events = run_engine(make_frames({90}), shutter_key="F")
    assert stats.strikes == 1
    assert presses == ["F"]
    assert any(e.kind == "strike" for e in events)


def test_no_lightning_no_photos():
    stats, presses, _ = run_engine(make_frames(set()))
    assert stats.strikes == 0
    assert presses == []


def test_test_mode_logs_but_does_not_press():
    stats, presses, events = run_engine(make_frames({90}), armed=False)
    assert stats.strikes == 1
    assert presses == []
    assert any(e.kind == "strike" and "[test]" in e.message for e in events)


def test_cooldown_ignores_quick_follow_up_flashes():
    frames = make_frames({60, 75}, seconds=2.0)
    stats, presses, _ = run_engine(frames, cooldown_s=2.0)
    assert stats.strikes == 1
    assert presses == ["F"]


def test_photo_limit_disarms():
    frames = make_frames({60, 150}, seconds=4.0)
    stats, presses, events = run_engine(frames, max_photos=2, cooldown_s=0.5)
    assert presses == ["F", "F"]
    assert any(e.kind == "done" for e in events)
    assert not stats.armed


def test_paused_while_the_game_is_not_in_front():
    seen = []

    def other_window(game):
        seen.append(game)
        return False, "explorer.exe has focus, not Game.exe"

    cfg = AppConfig(regions=[Region(0, 0, 640, 200)], regions_ref_size=(640, 360), sound=False, game_exe="Game.exe")
    cap = FakeCapture(make_frames({90}))
    sender = FakeSender()
    engine = Engine(cfg, capture_factory=lambda _: cap, sender=sender, sound=lambda _: None, focus_check=other_window)
    engine.start()
    engine.set_armed(True)
    time.sleep(0.5)
    stats = engine.snapshot()
    engine.stop()
    events = []
    while not engine.events.empty():
        events.append(engine.events.get())
    assert seen and seen[0] == "Game.exe"
    assert stats.paused and stats.strikes == 0
    assert cap.i == 0  # no frames examined
    assert sender.presses == []
    assert any(e.kind == "info" and e.message.startswith("Paused") for e in events)


def test_pausing_can_be_turned_off():
    stats, presses, _ = run_engine(make_frames({90}), focus_check=lambda _: (False, "x"), game_in_front_only=False)
    assert stats.strikes == 1
    assert presses == ["F"]


BOLT_AND_EMPTY = [Region(0, 0, 640, 200), Region(0, 250, 640, 110)]  # the bolt is only in the first
BOLT_TWICE = [Region(0, 0, 640, 100), Region(0, 100, 640, 100)]  # the bolt crosses both


@pytest.mark.parametrize(
    "mode, regions, strikes",
    [("any", BOLT_AND_EMPTY, 1), ("all", BOLT_AND_EMPTY, 0), ("all", BOLT_TWICE, 1)],
)
def test_lightning_in_any_or_all_regions(mode, regions, strikes):
    stats, _, _ = run_engine(make_frames({90}), regions=regions, trigger_mode=mode)
    assert stats.strikes == strikes


def test_hold_time_is_random_within_the_range():
    cfg = AppConfig(key_hold_min_ms=50, key_hold_max_ms=100)
    holds = {cfg.random_hold_ms() for _ in range(200)}
    assert min(holds) >= 50 and max(holds) <= 100 and len(holds) > 10


def test_background_photos_need_the_game_visible_not_focused():
    def not_focused(_game):
        return False, "browser.exe has focus, not Game.exe"

    stats, presses, _ = run_engine(
        make_frames({90}), focus_check=not_focused, background_photos=True, game_exe="Game.exe"
    )
    assert stats.strikes == 1 and presses == ["F"]
