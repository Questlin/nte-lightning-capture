"""Lightning detection on raw screen frames.

The detector runs per region on every captured frame, so a strike is
recognised as soon as it appears. It was tuned on recordings of NTE's photo
mode with rain, a moving character, flying fish and rotating sun rays.

A pixel counts as *brightened* when, compared with how it looked a short
moment ago (``ramp_s``), it has become much brighter (``jump``), is still
getting brighter right now, and is bright in absolute terms
(``min_brightness``). "A moment ago" is a per-pixel peak that slowly fades,
so lights that blink or were lit recently do not count. Bolts in NTE often
fade in over a few frames, which is why the comparison spans ``ramp_s``
instead of a single frame.

A pixel counts as *darkened* when it became much darker over the same span.
Moving things (the character, fish, sun rays, clouds) brighten pixels on one
side and darken them on the other; a lightning bolt only brightens. So each
small patch of the region scores::

    % brightened - dark_weight * % darkened

where darkening is counted over the patch and those around it (objects are
often bigger than one patch). The region's score is its best patch. Patches
keep a thin bolt from being diluted in a big region, and the darkening
penalty cancels motion.

Sun beams are the one thing that looks most like a bolt: a lens-flare streak
that flicks on in place. They are straight lines that run back to the sun,
so a candidate whose brightened pixels form a straight streak pointing at a
saturated disk, with bright pixels all the way there, is ignored
(``ignore_sun_rays``).

Each patch also learns how busy it normally is (waves breaking, flickering
UI, rays sweeping by): a slowly fading memory of its recent scores. A patch
must beat ``busy_margin`` times that memory, as well as the trigger level,
so a spot that keeps almost triggering needs a stronger flash than quiet
sky. The reported score is relative to each patch's own threshold.

An optional "sky flash" check compares the region's average brightness with
a slow moving average, for strikes that light up the whole sky.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass

import numpy as np


@dataclass
class DetectorSettings:
    # Score (best patch's % of pixels brightened, minus darkened) that triggers.
    trigger_pct: float = 8.0
    # How much brighter (0-255) a pixel must get within ``ramp_s``.
    jump: int = 40
    # Minimum absolute brightness (0-255) of a brightened pixel.
    min_brightness: int = 120
    # Bolts may take this long to fade in; compare against this far back.
    ramp_s: float = 0.05
    # Brightening per frame needed to count as "getting brighter right now".
    step: int = 5
    # Penalty per darkened pixel (cancels moving objects). 0 turns it off.
    dark_weight: float = 1.5
    # Patch size as a fraction of the display height (0.09 = 97 px at 1080p).
    patch_frac: float = 0.09
    # Rise of the region's average brightness that counts as a sky flash.
    # 0 turns the check off.
    flash_jump: float = 0.0
    # How fast the remembered per-pixel peak fades (brightness levels per second).
    decay_per_sec: float = 50.0
    # Upper bound of pixels examined per region; bigger regions are sampled
    # on a grid so processing stays well inside one frame.
    max_samples: int = 300_000
    # Ignore triggers for this long after (re)starting so the baseline settles.
    warmup_s: float = 0.5
    # Ignore straight streaks that run back to the sun (lens-flare beams).
    ignore_sun_rays: bool = True
    # A patch must beat this many times its own recent activity. 0 turns
    # the "busy spot" learning off.
    busy_margin: float = 1.5
    # How long a busy spot stays remembered (half-life, seconds).
    busy_memory_s: float = 30.0

    def clamp(self) -> "DetectorSettings":
        self.trigger_pct = float(min(100.0, max(0.1, self.trigger_pct)))
        self.jump = int(min(255, max(1, self.jump)))
        self.min_brightness = int(min(255, max(0, self.min_brightness)))
        self.ramp_s = float(min(1.0, max(0.0, self.ramp_s)))
        self.step = int(min(255, max(0, self.step)))
        self.dark_weight = float(min(10.0, max(0.0, self.dark_weight)))
        self.patch_frac = float(min(1.0, max(0.01, self.patch_frac)))
        self.flash_jump = float(min(255.0, max(0.0, self.flash_jump)))
        self.decay_per_sec = float(max(1.0, self.decay_per_sec))
        self.max_samples = int(max(1_000, self.max_samples))
        self.warmup_s = float(max(0.0, self.warmup_s))
        self.ignore_sun_rays = bool(self.ignore_sun_rays)
        self.busy_margin = float(min(10.0, max(0.0, self.busy_margin)))
        self.busy_memory_s = float(min(3600.0, max(1.0, self.busy_memory_s)))
        return self


@dataclass
class RegionResult:
    # Best patch: % brightened minus weighted % darkened, scaled to the trigger
    # level when the patch is a busy spot. Sun beams are left out.
    score: float
    flash: float  # rise of mean brightness above its moving average
    triggered: bool
    warming_up: bool
    # Best patch as (x, y, w, h) in the region's pixel coordinates.
    patch: tuple[int, int, int, int] = (0, 0, 0, 0)


def luminance(pixels: np.ndarray) -> np.ndarray:
    """Brightest colour channel of BGR(A) pixels, as uint8.

    The max channel rather than a weighted luma keeps saturated blue/violet
    bolts from looking dim.
    """
    return np.maximum(np.maximum(pixels[..., 0], pixels[..., 1]), pixels[..., 2])


def sample_stride(width: int, height: int, max_samples: int) -> int:
    area = max(1, width * height)
    if area <= max_samples:
        return 1
    return int(math.ceil(math.sqrt(area / max_samples)))


def _patch_layout(length: int, patch: int) -> tuple[int, int]:
    """Number of patches along an axis and their size, covering it evenly."""
    count = max(1, length // max(1, patch))
    return count, length // count


class SunView:
    """Where the sun is in a frame, for telling its beams from bolts.

    Suns are the largest fully saturated disks that were also there in an
    earlier frame (``steady``), so a bolt's own glare never counts as one.
    A pixel is on a sun beam
    when the straight line from it to a sun is lit nearly all the way: a
    beam runs right back to the sun, while a bolt has dark sky in between.
    """

    LEVEL = 245  # brightness of the sun's (saturated) disk
    BLOCK_FRAC = 0.012  # block size for finding the disk, as a fraction of the display height
    MIN_BLOCKS = 3.0  # saturated blocks (within 5 x 5) a sun needs
    MAX_SUNS = 1
    LIT = 0.85  # share of the way to the sun that must be lit
    # Far from the sun a beam can be dim; there it counts as lit where it is
    # brighter than the sky this many degrees to the side.
    SIDE_TAN = math.tan(math.radians(6))
    RIDGE = 8
    STREAK = 0.45  # most a beam's width may be, relative to its length

    def __init__(
        self, pixels: np.ndarray, display_height: int, settings: DetectorSettings, steady: np.ndarray | None = None
    ):
        h, w = pixels.shape[:2]
        self.stride = s = sample_stride(w, h, settings.max_samples)
        self.lum = lum = luminance(pixels[::s, ::s])
        # Lit means clearly brighter than the typical screen, and bright.
        self.lit_level = max(settings.min_brightness, float(np.median(lum[::3, ::3])) + settings.jump)
        b = max(2, int(round(self.BLOCK_FRAC * display_height / s)))
        self.radius = 2.0 * b
        gy, gx = lum.shape[0] // b, lum.shape[1] // b
        suns = []
        # Share of saturated pixels per block.
        self.saturated = (lum[: gy * b, : gx * b] >= self.LEVEL).reshape(gy, b, gx, b).mean(axis=(1, 3))
        if gy and gx and steady is not None and steady.shape == self.saturated.shape:
            sat = np.minimum(self.saturated, steady)
            padded = np.pad(sat, 2)
            mass = sum(padded[dy : dy + gy, dx : dx + gx] for dy in range(5) for dx in range(5))
            mass[sat < 0.75] = 0.0
            for _ in range(self.MAX_SUNS):
                j = int(mass.argmax())
                if mass.flat[j] < self.MIN_BLOCKS:
                    break
                by, bx = divmod(j, gx)
                suns.append(((by + 0.5) * b, (bx + 0.5) * b))
                mass[max(0, by - 4) : by + 5, max(0, bx - 4) : bx + 5] = 0.0
        self.suns = np.array(suns, np.float64).reshape(-1, 2)

    def is_streak(self, ys: np.ndarray, xs: np.ndarray) -> bool:
        """Whether points (full-resolution frame coordinates) are narrow as seen
        from the sun: a beam is long along the line to the sun and thin across
        it, while a bolt's glow or a jagged bolt is not."""
        if not len(self.suns) or len(ys) < 2:
            return False
        sy, sx = self.suns[0] * self.stride
        r = np.hypot(ys - sy, xs - sx)
        angle = np.arctan2(ys - sy, xs - sx)
        across = r.mean() * np.std(np.unwrap(angle))
        return across <= self.STREAK * max(float(np.std(r)), 1e-6)

    def on_rays(self, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        """For points in full-resolution frame coordinates, whether each lies on a lit line to a sun."""
        lum, s = self.lum, self.stride
        h, w = lum.shape
        ys = np.asarray(ys, np.float64) / s
        xs = np.asarray(xs, np.float64) / s
        out = np.zeros(len(ys), bool)
        for sy, sx in self.suns:
            dy, dx = sy - ys, sx - xs
            dist = np.hypot(dy, dx)
            near = dist <= self.radius
            steps = int(min(dist.max(), h + w) / 2) + 1
            # From each point to the edge of the sun's disk, a sample every two
            # pixels of the longest path, taking the brightest of the line and the
            # pixels either side of it.
            end = np.clip((dist - self.radius) / np.maximum(dist, 1e-9), 0.0, 1.0)
            tt = (np.arange(steps) / steps)[None, :] * end[:, None]
            ny, nx = -dx / np.maximum(dist, 1e-9), dy / np.maximum(dist, 1e-9)
            py = ys[:, None] + dy[:, None] * tt
            px = xs[:, None] + dx[:, None] * tt

            def sample(off: np.ndarray | float) -> np.ndarray:
                yy = np.clip(np.rint(py + off * ny[:, None]).astype(np.intp), 0, h - 1)
                xx = np.clip(np.rint(px + off * nx[:, None]).astype(np.intp), 0, w - 1)
                return lum[yy, xx]

            path = np.maximum(np.maximum(sample(-1.0), sample(0.0)), sample(1.0))
            # Beside the path, a few degrees either way as seen from the sun.
            # Only the darker side counts: near the sun another spike may run
            # alongside.
            side = np.maximum(3.0, (dist[:, None] * (1.0 - tt)) * self.SIDE_TAN)
            sides = np.minimum(sample(side), sample(-side)).astype(np.int16)
            lit = (path >= self.lit_level) | (path >= sides + self.RIDGE)
            out |= near | (lit.mean(axis=1) >= self.LIT)
        return out


class RegionDetector:
    """Stateful detector for one screen region."""

    MEAN_AVG_TIME_S = 0.5
    SPREAD = 1  # neighbouring patches (each way) counted for darkening
    BUSY_CAP = 2.0  # a busy spot needs at most busy_margin * BUSY_CAP * trigger
    MAX_CANDIDATES = 8  # patches checked for sun beams per frame
    RAY_SAMPLES = 32  # brightened pixels per patch checked against the sun
    RAY_SHARE = 0.6  # share of them on sun beams that makes the patch a beam
    SUN_REFRESH_S = 0.5  # how often to note where saturated (sun-like) spots are

    def __init__(self, width: int, height: int, settings: DetectorSettings, display_height: int | None = None):
        self.width = width
        self.height = height
        self.settings = settings
        self.stride = sample_stride(width, height, settings.max_samples)
        display_height = display_height or height
        patch = settings.patch_frac * display_height / self.stride
        sh, sw = -(-height // self.stride), -(-width // self.stride)
        self._gy, self._ph = _patch_layout(sh, int(round(patch)))
        self._gx, self._pw = _patch_layout(sw, int(round(patch)))
        self.display_height = display_height
        self.reset()

    def reset(self) -> None:
        self._ref: np.ndarray | None = None
        self._prev: np.ndarray | None = None
        self._recent: collections.deque[tuple[float, np.ndarray]] = collections.deque()
        self._mean_avg = 0.0
        self._last_t = 0.0
        self._decay_acc = 0.0
        self._warm_until = 0.0
        self._busy = np.zeros((self._gy, self._gx))
        self._steady: np.ndarray | None = None
        self._steady_t = -math.inf

    def sample(self, region_pixels: np.ndarray) -> np.ndarray:
        s = self.stride
        if s > 1:
            region_pixels = region_pixels[::s, ::s]
        return luminance(region_pixels)

    def _patch_pct(self, mask: np.ndarray, spread: bool = False) -> np.ndarray:
        """Percentage of each patch's pixels set in ``mask``.

        With ``spread``, each patch also counts the patches around it, so an object
        bigger than a patch still has its trailing edge counted against its
        leading edge.
        """
        gy, ph, gx, pw = self._gy, self._ph, self._gx, self._pw
        counts = mask[: gy * ph, : gx * pw].reshape(gy, ph, gx, pw).sum(axis=(1, 3), dtype=np.int32)
        if spread:
            r = self.SPREAD
            padded = np.pad(counts, r)
            n = 2 * r + 1
            counts = sum(padded[dy : dy + gy, dx : dx + gx] for dy in range(n) for dx in range(n))
        return counts * (100.0 / (ph * pw))

    def _sun_ray(self, bright: np.ndarray, py: int, px: int, sun: SunView, offset: tuple[int, int]) -> float | None:
        """If the brightening at patch (py, px) is a beam from the sun, its angle
        as seen from the sun (radians), else None.

        The brightened pixels in and around the patch must form a streak
        pointing at the sun, and most of the patch's own ones must lie on
        lit lines to it.
        """
        ph, pw, s = self._ph, self._pw, self.stride
        y0, x0 = max(0, (py - 1) * ph), max(0, (px - 1) * pw)
        ys, xs = np.nonzero(bright[y0 : (py + 2) * ph, x0 : (px + 2) * pw])
        ys += y0
        xs += x0
        # In display coordinates relative to the surrounding frame.
        fy, fx = ys * s + offset[1], xs * s + offset[0]
        own = (ys >= py * ph) & (ys < (py + 1) * ph) & (xs >= px * pw) & (xs < (px + 1) * pw)
        oy, ox = fy[own], fx[own]
        if not len(oy):
            return None
        # A streak within the patch, or across it and its neighbours (a wide
        # beam fills much of one patch; near the sun, other spikes crowd the
        # neighbours).
        step = max(1, len(fy) // (4 * self.RAY_SAMPLES))
        if not (sun.is_streak(oy, ox) or sun.is_streak(fy[::step], fx[::step])):
            return None
        step = max(1, len(oy) // self.RAY_SAMPLES)
        if sun.on_rays(oy[::step], ox[::step]).mean() < self.RAY_SHARE:
            return None
        sy, sx = sun.suns[0] * sun.stride
        return math.atan2(oy.mean() - sy, ox.mean() - sx)

    def _along_ray(self, sun: SunView, offset: tuple[int, int], angle: float) -> np.ndarray:
        """Patches whose centre lies on the beam leaving the sun at ``angle``."""
        s = self.stride
        sy, sx = sun.suns[0] * sun.stride
        cy = (np.arange(self._gy) + 0.5) * self._ph * s + offset[1] - sy
        cx = (np.arange(self._gx) + 0.5) * self._pw * s + offset[0] - sx
        along = cx[None, :] * math.cos(angle) + cy[:, None] * math.sin(angle)
        across = np.abs(cy[:, None] * math.cos(angle) - cx[None, :] * math.sin(angle))
        return (along > 0) & (across <= 0.75 * max(self._ph, self._pw) * s)

    def update(
        self, region_pixels: np.ndarray, now: float, surround: tuple[np.ndarray, int, int] | None = None
    ) -> RegionResult:
        """Feed the region's pixels (H x W x 3/4, BGR order) of a new frame.

        ``surround`` is an optional larger frame holding the region at (x, y),
        such as the whole screen; sun beams are traced back to a sun anywhere
        in it rather than only inside the region.
        """
        st = self.settings
        lum = self.sample(region_pixels).astype(np.int16)

        if self._ref is None or self._ref.shape != lum.shape:
            self.reset()
            self._ref = lum.copy()
            self._prev = lum
            self._mean_avg = float(lum.mean())
            self._last_t = now
            self._warm_until = now + st.warmup_s
            return RegionResult(0.0, 0.0, False, True)

        dt = min(0.25, max(0.0, now - self._last_t))
        self._last_t = now
        ref, prev = self._ref, self._prev
        # Frame from about ``ramp_s`` ago (or the previous one).
        past = self._recent[0][1] if self._recent else prev

        bright = (lum - ref >= st.jump) & (lum >= st.min_brightness)
        if st.step > 0:
            bright &= lum - prev >= st.step
        score_map = self._patch_pct(bright)
        if st.dark_weight > 0:
            score_map -= st.dark_weight * self._patch_pct(lum - past <= -st.jump, spread=True)
        np.maximum(score_map, 0.0, out=score_map)
        # Score relative to each patch's own threshold, so busy spots need more.
        rel = score_map
        if st.busy_margin > 0:
            rel = score_map * (st.trigger_pct / np.maximum(st.trigger_pct, st.busy_margin * self._busy))
        sun = None
        for _ in range(self.MAX_CANDIDATES):
            best = int(rel.argmax())
            if rel.flat[best] < st.trigger_pct or not st.ignore_sun_rays:
                break
            if sun is None:
                frame, ox, oy = surround if surround is not None else (region_pixels, 0, 0)
                sun = SunView(frame, self.display_height, st, steady=self._steady)
                offset = (ox, oy)
                if not len(sun.suns):
                    break
            angle = self._sun_ray(bright, *divmod(best, self._gx), sun, offset)
            if angle is None:
                break
            # Leave out this patch and the rest of the same beam.
            if rel is score_map:
                rel = score_map.copy()
            rel.flat[best] = 0.0
            rel[self._along_ray(sun, offset, angle)] = 0.0
        best = int(rel.argmax())
        score = float(rel.flat[best])
        py, px = divmod(best, self._gx)
        s = self.stride
        patch = (px * self._pw * s, py * self._ph * s, self._pw * s, self._ph * s)

        if st.ignore_sun_rays and now - self._steady_t >= self.SUN_REFRESH_S:
            if sun is None:
                sun = SunView(surround[0] if surround is not None else region_pixels, self.display_height, st)
            self._steady = sun.saturated
            self._steady_t = now
        if st.busy_margin > 0:
            self._busy *= 0.5 ** (dt / st.busy_memory_s)
            np.maximum(self._busy, np.minimum(score_map, self.BUSY_CAP * st.trigger_pct), out=self._busy)

        mean = float(lum.mean())
        flash = mean - self._mean_avg
        alpha = 1.0 - math.exp(-dt / self.MEAN_AVG_TIME_S) if dt > 0 else 0.0
        self._mean_avg += alpha * (mean - self._mean_avg)

        # Fade the remembered peak, then fold in frames older than ramp_s.
        self._decay_acc += st.decay_per_sec * dt
        decay = int(self._decay_acc)
        if decay:
            self._decay_acc -= decay
            np.subtract(ref, decay, out=ref)
        self._recent.append((now, lum))
        while self._recent and now - self._recent[0][0] >= st.ramp_s:
            np.maximum(ref, self._recent.popleft()[1], out=ref)
        self._prev = lum

        warming = now < self._warm_until
        hit = score >= st.trigger_pct or (st.flash_jump > 0 and flash >= st.flash_jump)
        return RegionResult(score, flash, hit and not warming, warming, patch)
