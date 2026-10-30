"""``--self-test``: checks that a build has everything it needs.

Used by CI on the packaged .exe, and handy for bug reports: it writes a short
report (to the given file, or stdout) and exits non-zero only when a required
module is missing.
"""

from __future__ import annotations

import platform
import sys
import time


def run(report_path: str | None = None) -> int:
    lines = [f"Python {sys.version.split()[0]} on {platform.platform()}"]
    ok = True

    def check(label, fn, required=True):
        nonlocal ok
        try:
            lines.append(f"OK   {label}: {fn()}")
        except Exception as exc:
            lines.append(f"{'FAIL' if required else 'WARN'} {label}: {exc!r}")
            ok = ok and not required

    check("numpy", lambda: __import__("numpy").__version__)
    check("Pillow", lambda: __import__("PIL").__version__)
    check("ImageTk", lambda: bool(__import__("PIL.ImageTk", fromlist=["x"])))
    check("tkinter", lambda: __import__("tkinter").TkVersion)
    if sys.platform == "win32":
        check("dxcam", lambda: __import__("dxcam").__name__)
        check("winput", lambda: __import__("nte_lightning.winput", fromlist=["x"]).INPUT.__name__)
    check("mss", lambda: __import__("mss").__name__, required=sys.platform != "win32")
    check("app modules", lambda: __import__("nte_lightning.app", fromlist=["x"]).App.__name__)

    def capture():
        from .capture import list_monitors, open_capture

        mons = list_monitors()
        cap = open_capture(mons[0].index if mons else 0)
        t = time.perf_counter()
        frame = cap.grab_full_blocking(timeout_s=1.0)
        took = (time.perf_counter() - t) * 1000
        shape = None if frame is None else frame.shape
        return f"{[m.label for m in mons]} via {cap.name}, first frame {shape} in {took:.0f} ms"

    # A machine without a desktop session (like CI) may not be able to
    # capture, so this one is informational.
    check("screen capture", capture, required=False)

    text = "\n".join(lines) + "\n"
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0 if ok else 1
