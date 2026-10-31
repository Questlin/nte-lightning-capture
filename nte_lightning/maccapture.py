"""macOS screen capture backends.

ScreenCaptureKit is the main backend: like DXGI on Windows, it hands over
each new frame as macOS composes it. A Quartz window-list snapshot
(CGWindowListCreateImage) is the slower fallback.

Both capture at the display's size in points rather than Retina pixels
(e.g. 1512x982 on a 14" MacBook Pro instead of 3024x1964): a quarter of the
pixels to check, and bolts are still plenty big. Regions are stored in these
coordinates; the region picker scales its screenshot to fit, so this is
invisible to the user. Frames are H x W x 4 uint8 arrays in BGRA order, like
the Windows backends.
"""

from __future__ import annotations

import sys
import threading
import time

import numpy as np

from .capture import Box, Capture, MonitorInfo

Quartz = None
if sys.platform == "darwin":
    try:
        import Quartz
    except ImportError:
        Quartz = None

# kCVPixelFormatType_32BGRA ('BGRA')
_BGRA = int.from_bytes(b"BGRA", "big")
_LOCK_READ_ONLY = 1  # kCVPixelBufferLock_ReadOnly


class PermissionMissing(RuntimeError):
    pass


def _need_quartz() -> None:
    if Quartz is None:
        raise RuntimeError("the PyObjC packages are missing (pip install -r requirements.txt)")


def _check_permission() -> None:
    fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if fn is not None and not fn():
        raise PermissionMissing(
            "macOS hasn't allowed this app to record the screen. Turn it on in System Settings > Privacy & Security > "
            "Screen & System Audio Recording, then quit and reopen the app."
        )


def _display_ids() -> list[int]:
    _need_quartz()
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err:
        return [int(Quartz.CGMainDisplayID())]
    ids = [int(i) for i in ids[:count]]
    main = int(Quartz.CGMainDisplayID())
    # Main display first, so "Display 1" is the one with the menu bar.
    return sorted(ids, key=lambda i: i != main)


def _bounds(display_id: int) -> tuple[int, int, int, int]:
    """Display position and size in points: x, y, width, height."""
    r = Quartz.CGDisplayBounds(display_id)
    return int(r.origin.x), int(r.origin.y), int(r.size.width), int(r.size.height)


def list_monitors() -> list[MonitorInfo]:
    main = int(Quartz.CGMainDisplayID())
    out = []
    for i, did in enumerate(_display_ids()):
        _, _, w, h = _bounds(did)
        out.append(MonitorInfo(i, w, h, did == main))
    return out


def _display_for(monitor: int) -> int:
    ids = _display_ids()
    return ids[monitor] if monitor < len(ids) else ids[0]


def _wait(start, timeout_s: float, what: str):
    """Call ``start(handler)`` and wait for the handler's arguments."""
    done = threading.Event()
    result: list = []

    def handler(*args):
        result.extend(args)
        done.set()

    start(handler)
    if not done.wait(timeout_s):
        raise RuntimeError(f"macOS did not answer in time ({what})")
    return result


# --------------------------------------------------------------------------
# ScreenCaptureKit
# --------------------------------------------------------------------------

_output_class = None


def _make_output_class():
    """The stream output delegate class (defined lazily: PyObjC can only
    declare it once ScreenCaptureKit is loaded, and only once per process)."""
    global _output_class
    if _output_class is not None:
        return _output_class
    import objc
    import ScreenCaptureKit as SCK
    from Foundation import NSObject

    class NTEStreamOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
        target = objc.object_property()

        def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, kind):
            if kind != SCK.SCStreamOutputTypeScreen:
                return
            target = self.target
            if target is not None:
                target._on_sample(sample_buffer)

    _output_class = NTEStreamOutput
    return _output_class


class ScreenCaptureKitCapture(Capture):
    name = "ScreenCaptureKit"

    def __init__(self, monitor: int):
        super().__init__()
        _need_quartz()
        _check_permission()
        import CoreMedia
        import ScreenCaptureKit as SCK
        from dispatch import dispatch_queue_create

        self._CoreMedia = CoreMedia
        display_id = _display_for(monitor)
        _, _, self.width, self.height = _bounds(display_id)

        content, error = _wait(SCK.SCShareableContent.getShareableContentWithCompletionHandler_, 5.0, "screen list")
        if content is None:
            raise PermissionMissing(f"macOS refused the screen list: {error}")
        display = next((d for d in content.displays() if int(d.displayID()) == display_id), None)
        if display is None:
            raise RuntimeError(f"display {monitor + 1} not found by ScreenCaptureKit")

        flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setWidth_(self.width)
        cfg.setHeight_(self.height)
        cfg.setPixelFormat_(_BGRA)
        cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 120))  # as fast as the display updates
        cfg.setQueueDepth_(4)
        cfg.setShowsCursor_(False)

        self._frame_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_t = 0.0
        self._seq = 0
        self._taken = 0
        self._last: np.ndarray | None = None
        self._last_t = 0.0

        self._output = _make_output_class().alloc().init()
        self._output.target = self
        self._queue = dispatch_queue_create(b"nte.capture", None)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
        ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, SCK.SCStreamOutputTypeScreen, self._queue, None
        )
        if not ok:
            raise RuntimeError(f"could not add the stream output: {error}")
        (error,) = _wait(self._stream.startCaptureWithCompletionHandler_, 5.0, "start capture")
        if error is not None:
            raise RuntimeError(f"could not start capture: {error}")

    def _on_sample(self, sample_buffer) -> None:
        """Called by ScreenCaptureKit on its queue for every new frame."""
        try:
            pb = self._CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
        except Exception:
            return
        if pb is None:
            return  # an "idle" sample: nothing changed on screen
        Quartz.CVPixelBufferLockBaseAddress(pb, _LOCK_READ_ONLY)
        try:
            h = Quartz.CVPixelBufferGetHeight(pb)
            w = Quartz.CVPixelBufferGetWidth(pb)
            bpr = Quartz.CVPixelBufferGetBytesPerRow(pb)
            base = Quartz.CVPixelBufferGetBaseAddress(pb)
            if base is None:
                return
            raw = np.frombuffer(base.as_buffer(bpr * h), dtype=np.uint8)
            frame = raw.reshape(h, bpr // 4, 4)[:, :w].copy()  # copy before the buffer is reused
        finally:
            Quartz.CVPixelBufferUnlockBaseAddress(pb, _LOCK_READ_ONLY)
        with self._frame_lock:
            self._latest = frame
            self._latest_t = time.perf_counter()
            self._seq += 1

    def grab(self, box: Box | None = None) -> np.ndarray | None:
        with self._frame_lock:
            if self._seq == self._taken or self._latest is None:
                return None
            self._taken = self._seq
            frame, self._last_t = self._latest, self._latest_t
        if box is not None:
            l, t, r, b = box
            frame = frame[t:b, l:r]
        return frame

    def frame_age_ms(self) -> float | None:
        """Milliseconds since the last grabbed frame arrived from macOS."""
        if not self._last_t:
            return None
        return (time.perf_counter() - self._last_t) * 1000.0

    def close(self) -> None:
        stream, self._stream = getattr(self, "_stream", None), None
        if stream is not None:
            try:
                _wait(stream.stopCaptureWithCompletionHandler_, 2.0, "stop capture")
            except Exception:
                pass
        output = getattr(self, "_output", None)
        if output is not None:
            output.target = None


# --------------------------------------------------------------------------
# Quartz snapshot fallback
# --------------------------------------------------------------------------


class QuartzCapture(Capture):
    name = "Quartz snapshot (slower)"
    fast = False

    def __init__(self, monitor: int):
        super().__init__()
        _need_quartz()
        _check_permission()
        if not hasattr(Quartz, "CGWindowListCreateImage"):
            raise RuntimeError("CGWindowListCreateImage is not available on this macOS")
        self._x, self._y, self.width, self.height = _bounds(_display_for(monitor))
        # Fail now rather than in the capture loop.
        if self.grab((0, 0, 8, 8)) is None:
            raise RuntimeError("the screen snapshot came back empty")

    def grab(self, box: Box | None = None) -> np.ndarray | None:
        l, t, r, b = box or (0, 0, self.width, self.height)
        rect = Quartz.CGRectMake(self._x + l, self._y + t, r - l, b - t)
        image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageNominalResolution,  # points, not Retina pixels
        )
        if image is None:
            return None
        w, h = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
        bpr = Quartz.CGImageGetBytesPerRow(image)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
        frame = np.frombuffer(bytes(data), dtype=np.uint8).reshape(h, bpr // 4, 4)[:, :w]
        if frame.shape[:2] != (b - t, r - l):
            # Rounding at odd scale factors: pad or crop to the size asked for.
            out = np.zeros((b - t, r - l, 4), dtype=np.uint8)
            hh, ww = min(h, b - t), min(w, r - l)
            out[:hh, :ww] = frame[:hh, :ww]
            frame = out
        return frame


BACKENDS = (ScreenCaptureKitCapture, QuartzCapture)
