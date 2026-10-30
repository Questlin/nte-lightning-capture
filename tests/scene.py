"""Synthetic night-sky frames for exercising the detector."""

from __future__ import annotations

import numpy as np


class NightScene:
    """A dark, noisy sky with drifting clouds, blinking lights, a small moving
    light and a big bright moving object (like the character's arm), onto
    which lightning bolts can be drawn."""

    def __init__(self, width: int = 640, height: int = 360, seed: int = 1):
        self.w, self.h = width, height
        self.rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:height, 0:width]
        self.yy, self.xx = yy, xx
        # Static city lights: small bright squares, some of which blink.
        self.lights = [
            (int(self.rng.integers(0, width - 4)), int(self.rng.integers(height // 2, height - 4)))
            for _ in range(40)
        ]
        # A fixed bolt path, so a bolt fading in over several frames keeps its shape.
        x, path = width // 3, []
        for _ in range(int(height * 0.6)):
            x += int(self.rng.integers(-2, 3))
            path.append(x)
        self.bolt_path = path

    def frame(
        self, t: float, bolt: float = 0.0, sky_flash: float = 0.0, arm: bool = True, sun: bool = False, beam: float | None = None
    ) -> np.ndarray:
        """``bolt`` is the bolt's intensity, 0 (none) to 1 (full). ``sun`` draws
        a sun high on the right; ``beam`` adds a lens-flare beam from it at
        that angle (degrees, 0 = to the right, 90 = straight down)."""
        # Clouds: soft blobs drifting slowly across the frame.
        clouds = 25 + 20 * np.sin((self.xx + 30 * t) / 45.0) * np.cos((self.yy - 10 * t) / 35.0)
        img = clouds + self.rng.normal(0, 3, size=clouds.shape) + sky_flash
        img = np.clip(img, 0, 255)
        frame = np.zeros((self.h, self.w, 4), np.uint8)
        frame[..., 0] = np.clip(img * 1.3, 0, 255)  # bluish night sky (BGRA)
        frame[..., 1] = img
        frame[..., 2] = img * 0.8
        frame[..., 3] = 255
        # Blinking lights: half of them toggle every 0.5 s.
        for i, (x, y) in enumerate(self.lights):
            on = i % 2 == 0 or int(t * 2 + i) % 2 == 0
            if on:
                frame[y : y + 3, x : x + 3, :3] = 230
        # A small moving light (plane / particle).
        px = int(40 + 120 * t) % (self.w - 6)
        frame[20:26, px : px + 6, :3] = 255
        # A big bright object swinging back and forth (character's arm).
        if arm:
            ax = int(self.w * 0.55 + self.w * 0.15 * np.sin(t * 1.5))
            ay = int(self.h * 0.35)
            frame[ay : ay + 25, max(0, ax) : ax + 70, :3] = (235, 225, 215)
        if sun:
            self.draw_sun(frame, beam)
        if bolt > 0:
            self.draw_bolt(frame, bolt)
        return frame

    @property
    def sun_pos(self) -> tuple[int, int]:
        return int(self.w * 0.8), int(self.h * 0.15)

    def draw_sun(self, frame: np.ndarray, beam: float | None = None) -> None:
        sx, sy = self.sun_pos
        r = np.hypot(self.xx - sx, self.yy - sy)
        # Saturated disk with a glow fading out around it.
        glow = np.clip(255 * (1.6 - r / (self.h * 0.06)), 0, 255)
        if beam is not None:
            # A straight beam, a few pixels wide, fading with distance.
            a = np.radians(beam)
            along = (self.xx - sx) * np.cos(a) + (self.yy - sy) * np.sin(a)
            across = np.abs(-(self.xx - sx) * np.sin(a) + (self.yy - sy) * np.cos(a))
            on = (along > 0) & (across < 1.5 + along * 0.01)
            glow = np.maximum(glow, np.where(on, np.clip(230 - along * 0.15, 0, 255), 0))
        sky = frame[..., :3]
        frame[..., :3] = np.maximum(sky, glow[..., None].astype(np.uint8))

    def draw_bolt(self, frame: np.ndarray, intensity: float = 1.0) -> None:
        sky = frame[..., :3].astype(np.float32)
        for y, x in enumerate(self.bolt_path):
            # Bright core with a softer glow around it.
            for dx, level in ((0, 250), (1, 240), (-1, 240), (2, 190), (-2, 190), (3, 150), (-3, 150), (4, 120), (-4, 120)):
                xx = x + dx
                if 0 <= xx < self.w:
                    target = np.array((level, level * 0.95, level * 0.9))
                    sky[y, xx] = sky[y, xx] + (target - sky[y, xx]) * intensity
        frame[..., :3] = np.clip(sky, 0, 255).astype(np.uint8)
