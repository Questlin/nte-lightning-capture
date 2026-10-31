"""Small pieces shared by winput (Windows) and macinput (macOS)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PressResult:
    ok: bool
    message: str = ""


# Time the game gets to notice it has focus before the key goes down, and
# to read the last key release before focus goes back.
FOCUS_SETTLE_S = 0.1
