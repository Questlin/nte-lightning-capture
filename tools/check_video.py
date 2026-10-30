"""Replay a screen recording through the detector and save what it flags.

Needs ffmpeg on PATH. For every strike the detector reports, writes a JPEG
with the region outlined and the spot that triggered boxed in red.

    python tools/check_video.py recording.mp4 out_dir --region 0 420 1500 400
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nte_lightning.config import Region  # noqa: E402
from nte_lightning.detector import DetectorSettings, RegionDetector  # noqa: E402
from nte_lightning.engine import save_detection  # noqa: E402


def probe(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(num) / float(den)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="default: whole frame")
    ap.add_argument("--cooldown", type=float, default=1.0, help="seconds to ignore after a strike")
    ap.add_argument("--trigger", type=float, default=DetectorSettings().trigger_pct, help="trigger level")
    args = ap.parse_args()

    w, h, fps = probe(args.video)
    region = Region(*args.region) if args.region else Region(0, 0, w, h)
    det = RegionDetector(region.w, region.h, DetectorSettings(trigger_pct=args.trigger), display_height=h)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", args.video, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE
    )
    size = w * h * 3
    i, ready_at, found = 0, 0.0, 0
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        t = i / fps
        res = det.update(frame[region.y : region.y + region.h, region.x : region.x + region.w], t, surround=(frame, region.x, region.y))
        if res.triggered and t >= ready_at:
            ready_at = t + args.cooldown
            found += 1
            patch = (region.x + res.patch[0], region.y + res.patch[1], res.patch[2], res.patch[3])
            raw, marked = save_detection(frame, (region, patch, (0, 0)), out / f"{Path(args.video).stem}_t{t:06.2f}s")
            raw.unlink()
            print(f"t={t:6.2f}s  frame {i:5d}  score {res.score:5.1f}  -> {marked.name}")
        i += 1
    print(f"{found} strike(s) flagged in {i} frames.")


if __name__ == "__main__":
    main()
