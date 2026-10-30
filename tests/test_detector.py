import numpy as np
import pytest

from nte_lightning.detector import DetectorSettings, RegionDetector, luminance, sample_stride

from .scene import NightScene

FPS = 60.0


def make(scene, **settings):
    return RegionDetector(scene.w, scene.h, DetectorSettings(**settings))


def run(det, scene, seconds, start=0.0, **frame_kwargs):
    results = []
    for i in range(int(seconds * FPS)):
        t = start + i / FPS
        results.append(det.update(scene.frame(t, **frame_kwargs), t))
    return results


def test_luminance_uses_brightest_channel():
    px = np.array([[[200, 10, 10, 255], [10, 10, 90, 255]]], np.uint8)
    assert luminance(px).tolist() == [[200, 90]]


def test_sample_stride():
    assert sample_stride(100, 100, 300_000) == 1
    assert sample_stride(3840, 2160, 300_000) == 6


def test_background_motion_never_triggers():
    scene = NightScene()
    det = make(scene)
    results = run(det, scene, seconds=6)
    assert not any(r.triggered for r in results)
    assert max(r.score for r in results) < DetectorSettings().trigger_pct


def sliding_bar_frames(n=40):
    """A bright vertical bar sweeping across a dark sky, 6 px per frame."""
    rng = np.random.default_rng(3)
    for i in range(n):
        frame = np.full((200, 400, 4), 30, np.uint8)
        frame[..., :3] += rng.integers(0, 6, size=(200, 400, 3), dtype=np.uint8)
        x = 40 + 6 * i
        frame[:, x : x + 24, :3] = 230
        yield frame


@pytest.mark.parametrize("dark_weight, triggers", [(0.0, True), (1.5, False)])
def test_darkening_penalty_cancels_moving_objects(dark_weight, triggers):
    # Patches of 36 px, wider than the bar, as with a 400 px tall display.
    det = RegionDetector(400, 200, DetectorSettings(dark_weight=dark_weight, warmup_s=0.1), display_height=400)
    results = [det.update(f, i / FPS) for i, f in enumerate(sliding_bar_frames())]
    assert any(r.triggered for r in results) is triggers


def test_bolt_triggers_on_its_first_frame():
    scene = NightScene()
    det = make(scene)
    run(det, scene, seconds=2)
    res = det.update(scene.frame(2.0, bolt=1.0), 2.0)
    assert res.triggered
    assert res.score > 3 * DetectorSettings().trigger_pct


def test_bolt_fading_in_over_several_frames_triggers():
    scene = NightScene()
    det = make(scene)
    run(det, scene, seconds=2)
    # The bolt brightens over 6 frames (100 ms); each frame alone is a small step.
    hits = []
    for k in range(1, 7):
        t = 2.0 + k / FPS
        hits.append(det.update(scene.frame(t, bolt=k / 6), t).triggered)
    assert any(hits)
    # It is caught before reaching full brightness.
    assert hits.index(True) < 5


def test_warmup_suppresses_triggers():
    scene = NightScene()
    det = make(scene)
    det.update(scene.frame(0.0), 0.0)
    res = det.update(scene.frame(1 / FPS, bolt=1.0), 1 / FPS)
    assert res.warming_up and not res.triggered


def test_lingering_bolt_stops_triggering():
    scene = NightScene()
    det = make(scene)
    run(det, scene, seconds=2)
    results = [det.update(scene.frame(2.0 + k / FPS, bolt=1.0), 2.0 + k / FPS) for k in range(12)]
    assert results[0].triggered
    # A bolt that stays on screen is no longer sudden brightening.
    assert not results[-1].triggered


def test_sky_flash_check_is_optional():
    scene = NightScene()
    off = make(scene)
    on = make(scene, flash_jump=20)
    for det in (off, on):
        run(det, scene, seconds=2)
    frame = scene.frame(2.0, sky_flash=40)
    assert not off.update(frame, 2.0).triggered
    assert on.update(frame, 2.0).triggered


@pytest.mark.parametrize("budget", [300_000, 20_000])
def test_large_regions_are_sampled_and_still_detect(budget):
    scene = NightScene(width=1280, height=720)
    det = make(scene, max_samples=budget)
    run(det, scene, seconds=1.5)
    assert det.update(scene.frame(1.5, bolt=1.0), 1.5).triggered


def sun_run(det, scene, seconds, start=0.0, **kw):
    return run(det, scene, seconds, start=start, sun=True, arm=False, **kw)


@pytest.mark.parametrize("angle", [100, 160, 200])
def test_sun_beam_flicking_on_is_ignored(angle):
    scene = NightScene()
    det = make(scene)
    sun_run(det, scene, seconds=2)
    results = sun_run(det, scene, seconds=0.3, start=2.0, beam=angle)
    assert not any(r.triggered for r in results)
    # Without the check the same beam does trigger.
    det = make(scene, ignore_sun_rays=False)
    sun_run(det, scene, seconds=2)
    assert any(r.triggered for r in sun_run(det, scene, seconds=0.3, start=2.0, beam=angle))


def test_bolt_still_triggers_with_the_sun_in_view():
    scene = NightScene()
    det = make(scene)
    sun_run(det, scene, seconds=2)
    assert det.update(scene.frame(2.0, bolt=1.0, sun=True, arm=False), 2.0).triggered


def test_sun_beam_traced_to_a_sun_outside_the_region():
    scene = NightScene()
    # Region: the lower part of the screen, without the sun.
    y0 = scene.h // 2
    det = RegionDetector(scene.w, scene.h - y0, DetectorSettings(), display_height=scene.h)
    hits = []
    for i in range(int(2.3 * FPS)):
        t = i / FPS
        frame = scene.frame(t, sun=True, arm=False, beam=110 if t >= 2.0 else None)
        hits.append(det.update(frame[y0:], t, surround=(frame, 0, y0)).triggered)
    assert not any(hits)


def flicker_frames(seconds, start=0.0, bolt_at=None):
    """Dark sky with one spot that lights up briefly every second (a wave
    breaking, a blinking UI element), and optionally a bolt elsewhere."""
    rng = np.random.default_rng(5)
    for i in range(int(seconds * FPS)):
        t = start + i / FPS
        frame = np.full((200, 400, 4), 30, np.uint8)
        frame[..., :3] += rng.integers(0, 6, size=(200, 400, 3), dtype=np.uint8)
        if 0.5 <= (t % 1.0) < 0.6:
            frame[150:160, 40:60, :3] = 220  # about 15% of a 36 px patch
        if bolt_at is not None and abs(t - bolt_at) < 1e-6:
            frame[20:120, 300:304, :3] = 240
        yield t, frame


def test_busy_spot_needs_more_after_a_while():
    det = RegionDetector(400, 200, DetectorSettings(warmup_s=0.1), display_height=400)
    hits = [(t, det.update(f, t).triggered) for t, f in flicker_frames(6.0)]
    assert any(h for t, h in hits if t < 1.0)  # the first flicker counts
    assert not any(h for t, h in hits if t > 2.5)  # then the spot is known as busy
    # A bolt somewhere quiet still triggers.
    results = [det.update(f, t) for t, f in flicker_frames(0.2, start=6.0, bolt_at=6.1)]
    assert any(r.triggered for r in results)


def test_busy_spot_learning_can_be_turned_off():
    det = RegionDetector(400, 200, DetectorSettings(warmup_s=0.1, busy_margin=0), display_height=400)
    hits = [(t, det.update(f, t).triggered) for t, f in flicker_frames(6.0)]
    assert any(h for t, h in hits if t > 2.0)
