"""Screen capture backends.

``dxcam`` (DXGI Desktop Duplication) is the main backend: it hands over each
new frame the moment Windows composes it, with no polling of unchanged
frames, which is what makes single-frame lightning detection possible.
``mss`` (GDI) is a slower fallback for systems where DXGI duplication is not
available.

All backends return frames as H x W x 4 uint8 arrays in BGRA order.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

import numpy as np

Box = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass
class MonitorInfo:
    index: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        tag = " (primary)" if self.primary else ""
        return f"Display {self.index + 1}: {self.width}x{self.height}{tag}"


class Capture:
    name = "base"
    fast = True

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.width = 0
        self.height = 0

    def grab(self, box: Box | None = None) -> np.ndarray | None:
        """Return the newest frame (or part of it), or None if nothing new.

        The array may be a buffer the backend reuses on the next grab; copy
        it to keep it.
        """
        raise NotImplementedError

    def frame_age_ms(self) -> float | None:
        """Milliseconds since the last grabbed frame was presented, if known."""
        return None

    def grab_full_blocking(self, timeout_s: float = 1.5) -> np.ndarray | None:
        end = time.perf_counter() + timeout_s
        while time.perf_counter() < end:
            with self.lock:
                frame = self.grab(None)
            if frame is not None:
                return frame.copy()
            time.sleep(0.01)
        return None

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------
# dxcam
# --------------------------------------------------------------------------

_dx_cameras: dict[int, object] = {}


class DxcamCapture(Capture):
    name = "DXGI (dxcam)"

    def __init__(self, monitor: int):
        super().__init__()
        import dxcam

        cam = _dx_cameras.get(monitor)
        if cam is None or getattr(cam, "is_released", False):
            # dxcam keeps one camera per display; reuse it for the whole
            # session instead of releasing and recreating.
            cam = dxcam.create(output_idx=monitor, output_color="BGRA")
            _dx_cameras[monitor] = cam
        self._cam = cam
        self.width, self.height = cam.width, cam.height
        self._qpc = None
        self._qpf = None
        try:
            import ctypes

            freq = ctypes.c_longlong()
            ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
            self._qpf = freq.value
            self._qpc = ctypes.windll.kernel32.QueryPerformanceCounter
        except (AttributeError, OSError):
            pass

    def grab(self, box: Box | None = None) -> np.ndarray | None:
        return self._cam.grab(region=box, copy=False)

    def frame_age_ms(self) -> float | None:
        dup = getattr(self._cam, "_duplicator", None)
        ticks = getattr(dup, "latest_frame_ticks", 0)
        if not ticks or not self._qpc or not self._qpf:
            return None
        import ctypes

        now = ctypes.c_longlong()
        self._qpc(ctypes.byref(now))
        age = (now.value - ticks) * 1000.0 / self._qpf
        return age if 0 <= age < 1000 else None


# --------------------------------------------------------------------------
# mss fallback
# --------------------------------------------------------------------------


def _mss_factory():
    import mss

    return getattr(mss, "MSS", None) or mss.mss


class MssCapture(Capture):
    name = "GDI (mss, slower)"
    fast = False

    def __init__(self, monitor: int):
        super().__init__()
        self._local = threading.local()
        with _mss_factory()() as sct:
            mons = sct.monitors[1:]
            mon = mons[monitor] if monitor < len(mons) else sct.monitors[1]
        self._mon = dict(mon)
        self.width, self.height = mon["width"], mon["height"]

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = _mss_factory()()
        return sct

    def grab(self, box: Box | None = None) -> np.ndarray | None:
        l, t, r, b = box or (0, 0, self.width, self.height)
        shot = self._sct().grab(
            {"left": self._mon["left"] + l, "top": self._mon["top"] + t, "width": r - l, "height": b - t}
        )
        return np.asarray(shot)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

_OUTPUT_RE = re.compile(r"Device\[0\] Output\[(\d+)\]: Res:\((\d+), (\d+)\) Rot:(\d+) Primary:(\w+)")


def list_monitors() -> list[MonitorInfo]:
    try:
        import dxcam

        found = []
        for m in _OUTPUT_RE.finditer(dxcam.output_info()):
            idx, w, h, rot, primary = m.groups()
            w, h = int(w), int(h)
            if int(rot) in (90, 270):
                w, h = h, w
            found.append(MonitorInfo(int(idx), w, h, primary == "True"))
        if found:
            return found
    except Exception:
        pass
    try:
        with _mss_factory()() as sct:
            return [MonitorInfo(i, m["width"], m["height"], i == 0) for i, m in enumerate(sct.monitors[1:])]
    except Exception:
        return []


def open_capture(monitor: int) -> Capture:
    """Open the fastest available backend for the given display."""
    errors = []
    for cls in (DxcamCapture, MssCapture):
        try:
            return cls(monitor)
        except Exception as exc:  # backend missing or unsupported on this PC
            errors.append(f"{cls.name}: {exc}")
    raise RuntimeError("No screen capture backend works here. " + "; ".join(errors))
