"""macOS input and system helpers: the Mac side of winput.

winput imports this module on macOS and uses its names in place of the
Windows ones, so the rest of the app stays the same on both systems.

Key presses go through Quartz events (CGEventPost), which needs the
Accessibility permission. Focus and window checks use NSWorkspace and the
window list, which need no permission. Nothing here needs administrator
rights.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

from .inputcommon import FOCUS_SETTLE_S, PressResult

IS_MAC = sys.platform == "darwin"

Quartz = AppKit = ApplicationServices = None
if IS_MAC:
    try:
        import AppKit
        import ApplicationServices
        import Quartz
    except ImportError:  # running from source without the PyObjC packages
        Quartz = AppKit = ApplicationServices = None

# --------------------------------------------------------------------------
# Key names -> macOS virtual key codes (kVK_* in Carbon's Events.h)
# --------------------------------------------------------------------------

KEY_NAMES: dict[str, int] = {
    "A": 0x00, "S": 0x01, "D": 0x02, "F": 0x03, "H": 0x04, "G": 0x05, "Z": 0x06, "X": 0x07,
    "C": 0x08, "V": 0x09, "B": 0x0B, "Q": 0x0C, "W": 0x0D, "E": 0x0E, "R": 0x0F, "Y": 0x10,
    "T": 0x11, "O": 0x1F, "U": 0x20, "I": 0x22, "P": 0x23, "L": 0x25, "J": 0x26, "K": 0x28,
    "N": 0x2D, "M": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C,
    "9": 0x19, "0": 0x1D,
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60, "F6": 0x61, "F7": 0x62,
    "F8": 0x64, "F9": 0x65, "F10": 0x6D, "F11": 0x67, "F12": 0x6F, "F13": 0x69, "F14": 0x6B,
    "F15": 0x71, "F16": 0x6A, "F17": 0x40, "F18": 0x4F, "F19": 0x50, "F20": 0x5A,
    "Space": 0x31,
    "Enter": 0x24,
    "Tab": 0x30,
    "Backspace": 0x33,
    "Escape": 0x35,
    "Shift": 0x38,
    "Ctrl": 0x3B,
    "Alt": 0x3A,  # Option
    "Command": 0x37,
    "Left Shift": 0x38,
    "Right Shift": 0x3C,
    "Left Ctrl": 0x3B,
    "Right Ctrl": 0x3E,
    "Left Alt": 0x3A,
    "Right Alt": 0x3D,
    "Right Command": 0x36,
    "Caps Lock": 0x39,
    "Delete": 0x75,  # forward delete
    "Home": 0x73,
    "End": 0x77,
    "Page Up": 0x74,
    "Page Down": 0x79,
    "Left": 0x7B,
    "Right": 0x7C,
    "Down": 0x7D,
    "Up": 0x7E,
    "Numpad 0": 0x52, "Numpad 1": 0x53, "Numpad 2": 0x54, "Numpad 3": 0x55, "Numpad 4": 0x56,
    "Numpad 5": 0x57, "Numpad 6": 0x58, "Numpad 7": 0x59, "Numpad 8": 0x5B, "Numpad 9": 0x5C,
    "Numpad *": 0x43,
    "Numpad +": 0x45,
    "Numpad -": 0x4E,
    "Numpad .": 0x41,
    "Numpad /": 0x4B,
    "`": 0x32,
    "-": 0x1B,
    "=": 0x18,
    "[": 0x21,
    "]": 0x1E,
    "\\": 0x2A,
    ";": 0x29,
    "'": 0x27,
    ",": 0x2B,
    ".": 0x2F,
    "/": 0x2C,
}

# name -> (down event type, up event type, button number)
MOUSE_BUTTONS: dict[str, tuple[int, int, int]] = {
    "Mouse Left": (1, 2, 0),  # kCGEventLeftMouseDown / Up
    "Mouse Right": (3, 4, 1),  # kCGEventRightMouseDown / Up
    "Mouse Middle": (25, 26, 2),  # kCGEventOtherMouseDown / Up
    "Mouse X1": (25, 26, 3),
    "Mouse X2": (25, 26, 4),
}

_MODIFIER_FLAGS = {
    0x38: 0x00020000, 0x3C: 0x00020000,  # shift
    0x3B: 0x00040000, 0x3E: 0x00040000,  # control
    0x3A: 0x00080000, 0x3D: 0x00080000,  # option
    0x37: 0x00100000, 0x36: 0x00100000,  # command
}

# Tk keysyms on macOS that differ from our names.
_KEYSYM_ALIASES = {
    "space": "Space",
    "Return": "Enter",
    "KP_Enter": "Enter",
    "BackSpace": "Backspace",
    "Shift_L": "Left Shift",
    "Shift_R": "Right Shift",
    "Control_L": "Left Ctrl",
    "Control_R": "Right Ctrl",
    "Option_L": "Left Alt",
    "Option_R": "Right Alt",
    "Alt_L": "Left Alt",
    "Alt_R": "Right Alt",
    "Meta_L": "Command",
    "Meta_R": "Right Command",
    "Command": "Command",
    "Prior": "Page Up",
    "Next": "Page Down",
    "Caps_Lock": "Caps Lock",
    "grave": "`",
    "minus": "-",
    "equal": "=",
    "bracketleft": "[",
    "bracketright": "]",
    "backslash": "\\",
    "semicolon": ";",
    "apostrophe": "'",
    "comma": ",",
    "period": ".",
    "slash": "/",
}

# Ways of sending the photo key, as shown in the UI. The second one goes
# straight to the game's process and needs no focus (see DIRECT_METHOD).
INPUT_METHODS = ["Keyboard event", "Send to game only"]
DIRECT_METHOD = INPUT_METHODS[1]


def all_input_names() -> list[str]:
    return list(KEY_NAMES) + list(MOUSE_BUTTONS)


def is_valid_input(name: str) -> bool:
    return name in KEY_NAMES or name in MOUSE_BUTTONS


def vk_for(name: str) -> int | None:
    return KEY_NAMES.get(name)


def name_from_tk_event(keycode: int, keysym: str) -> str | None:
    """Map a Tk key event to one of our key names (by keysym on macOS)."""
    if keysym in _KEYSYM_ALIASES:
        return _KEYSYM_ALIASES[keysym]
    if len(keysym) == 1 and keysym.upper() in KEY_NAMES:
        return keysym.upper()
    if keysym in KEY_NAMES:
        return keysym
    return None


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

SETTINGS_URLS = {
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


def screen_recording_allowed() -> bool | None:
    """Whether this app may capture the screen; None if it can't be told."""
    fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    return bool(fn()) if fn else None


def accessibility_allowed(prompt: bool = False) -> bool | None:
    """Whether this app may send key presses (Accessibility); with ``prompt``
    macOS shows its "would like to control this computer" dialog if not."""
    if ApplicationServices is None:
        return None
    try:
        if prompt:
            opts = {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
            return bool(ApplicationServices.AXIsProcessTrustedWithOptions(opts))
        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return None


def key_reading_allowed() -> bool | None:
    """Whether this app may read the keyboard (Input Monitoring), used by the
    arm hotkey; None if it can't be told."""
    fn = getattr(Quartz, "CGPreflightListenEventAccess", None)
    return bool(fn()) if fn else None


def request_permissions() -> list[str]:
    """Ask macOS for what the app needs, once per missing permission, and
    return a line for each one that is still missing."""
    missing = []
    if screen_recording_allowed() is False:
        req = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if req:
            req()  # adds the app to the list and shows macOS's prompt
        missing.append(
            "Screen Recording is not allowed yet, so the app can't see the game. Turn it on for this app in "
            "System Settings > Privacy & Security > Screen & System Audio Recording, then quit and reopen the app."
        )
    if accessibility_allowed(prompt=True) is False:
        missing.append(
            "Accessibility is not allowed yet, so the app can't press the photo key. Turn it on for this app in "
            "System Settings > Privacy & Security > Accessibility."
        )
    if key_reading_allowed() is False:
        req = getattr(Quartz, "CGRequestListenEventAccess", None)
        if req:
            req()
        missing.append(
            "Input Monitoring is not allowed yet, so the arm hotkey may not work while the game is in front. "
            "Turn it on for this app in System Settings > Privacy & Security > Input Monitoring, or use the Arm button."
        )
    return missing


def open_settings(kind: str) -> None:
    url = SETTINGS_URLS.get(kind)
    if url:
        subprocess.Popen(["open", url])


# --------------------------------------------------------------------------
# Apps and windows
# --------------------------------------------------------------------------

# Apps that stand for "the desktop" rather than a real window in front.
_SHELL_APPS = {"Finder", "Dock", "loginwindow", "Window Server"}


def _frontmost():
    if AppKit is None:
        return None
    return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()


def foreground_pid() -> int | None:
    app = _frontmost()
    return int(app.processIdentifier()) if app is not None else None


def foreground_title() -> str:
    app = _frontmost()
    return str(app.localizedName() or "") if app is not None else ""


def own_window_focused() -> bool:
    return foreground_pid() == os.getpid()


def foreground_app() -> str | None:
    """Name of the app in front (e.g. "Neverness to Everness").

    None when that is this app, the Finder/desktop, or nothing; "" when the
    name can't be read.
    """
    if AppKit is None:
        return ""
    app = _frontmost()
    if app is None or int(app.processIdentifier()) == os.getpid():
        return None
    name = str(app.localizedName() or "")
    if name in _SHELL_APPS:
        return None
    return name


def _game_pid(game: str) -> int | None:
    if AppKit is None or not game:
        return None
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if str(app.localizedName() or "").lower() == game.lower():
            return int(app.processIdentifier())
    return None


def game_in_front(game: str = "") -> tuple[bool, str]:
    """Whether the game is the app in front (windowed or fullscreen)."""
    if AppKit is None:
        return True, ""
    app = foreground_app()
    if app is None:
        return False, "this app or the desktop is in front" if foreground_pid() else "no app is in front"
    if game and app and app.lower() != game.lower():
        return False, f"{app} is in front, not {game}"
    return True, ""


def _on_screen_windows() -> list[dict]:
    """Normal windows on screen, front to back: pid, owner and bounds."""
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if int(w.get("kCGWindowLayer", 0)) != 0:
            continue  # menu bar, Dock, overlays
        b = w.get("kCGWindowBounds") or {}
        x, y = float(b.get("X", 0)), float(b.get("Y", 0))
        rect = (x, y, x + float(b.get("Width", 0)), y + float(b.get("Height", 0)))
        if rect[2] - rect[0] < 50 or rect[3] - rect[1] < 50:
            continue
        out.append({"pid": int(w.get("kCGWindowOwnerPID", 0)), "owner": str(w.get("kCGWindowOwnerName", "")), "rect": rect})
    return out


def find_game_window(game: str) -> int | None:
    """The game's process id if it has a window on screen, else None."""
    if Quartz is None or not game:
        return None
    pid = _game_pid(game)
    if pid is None:
        return None
    return pid if any(w["pid"] == pid for w in _on_screen_windows()) else None


def game_visible(game: str = "") -> tuple[bool, str]:
    """Whether the game's window can be seen, in front or not: running, on
    the current screen (not minimized or in another Space) and not covered
    by the app in front."""
    if Quartz is None:
        return True, ""
    if not game:
        return game_in_front(game)
    app = foreground_app()
    if app and app.lower() == game.lower():
        return True, ""
    pid = _game_pid(game)
    if pid is None:
        return False, f"{game} isn't running"
    windows = _on_screen_windows()
    mine = [w for w in windows if w["pid"] == pid]
    if not mine:
        return False, f"{game} is minimized or in another Space"
    game_rect = max(mine, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))["rect"]
    front = foreground_pid()
    for w in windows:
        if w["pid"] == pid:
            break  # the rest are behind the game
        a = w["rect"]
        if w["pid"] == front and a[0] < game_rect[2] and game_rect[0] < a[2] and a[1] < game_rect[3] and game_rect[1] < a[3]:
            return False, f"{w['owner'] or 'this app'} is in front of {game}"
    return True, ""


def _activate(pid: int) -> bool:
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    for _ in range(20):  # activation is asynchronous
        if foreground_pid() == pid:
            return True
        time.sleep(0.01)
    return foreground_pid() == pid


@contextlib.contextmanager
def game_focus(game: str):
    """Within the block the game is in front; afterwards the app that was in
    front gets it back. Does nothing without a ``game`` or when the game is
    already in front. Yields a message if switching failed."""
    pid = _game_pid(game) if AppKit is not None and game else None
    prev = foreground_pid() if pid else None
    if not pid or prev == pid:
        yield ""
        return
    if not _activate(pid):
        yield f"macOS didn't let this app switch to {game}"
        return
    time.sleep(FOCUS_SETTLE_S)
    try:
        yield ""
    finally:
        time.sleep(FOCUS_SETTLE_S)
        if prev:
            with contextlib.suppress(Exception):
                _activate(prev)


# --------------------------------------------------------------------------
# Windows-only features: no-ops on macOS
# --------------------------------------------------------------------------

RELAUNCHED_FLAG = "--relaunched"


def process_elevated(pid: int | None = None) -> bool | None:
    return None


def blocked_by_admin() -> str:
    """On macOS a missing Accessibility permission plays the part of the
    Windows administrator problem: key presses are silently dropped."""
    if accessibility_allowed() is False:
        return (
            "This app isn't allowed to press keys yet. Turn it on in System Settings > Privacy & Security > "
            "Accessibility (remove and re-add the app there after installing a new version)."
        )
    return ""


def relaunch_as_admin() -> bool:
    return False


def enable_dpi_awareness() -> None:
    pass


def high_resolution_timer(enable: bool) -> None:
    pass


# --------------------------------------------------------------------------
# Sending and reading keys
# --------------------------------------------------------------------------


def _cursor_location():
    return Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))


def make_key_event(name: str, down: bool):
    """A Quartz keyboard event for one of our key names (None if unknown)."""
    code = KEY_NAMES.get(name)
    if code is None or Quartz is None:
        return None
    event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
    if code in _MODIFIER_FLAGS:
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, _MODIFIER_FLAGS[code] if down else 0)
    else:
        Quartz.CGEventSetFlags(event, 0)  # no stray modifiers from the real keyboard
    return event


def make_mouse_event(name: str, down: bool):
    if name not in MOUSE_BUTTONS or Quartz is None:
        return None
    down_type, up_type, button = MOUSE_BUTTONS[name]
    event = Quartz.CGEventCreateMouseEvent(None, down_type if down else up_type, _cursor_location(), min(button, 2))
    if button > 2:
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber, button)
    return event


class InputSender:
    """Presses the photo key or mouse button in the app in front, or with
    "Send to game only" straight into the game's process."""

    def press(self, name: str, hold_ms: int, method: str = INPUT_METHODS[0], game: str = "") -> PressResult:
        if Quartz is None:
            return PressResult(False, "Sending input needs the PyObjC packages (pip install -r requirements.txt)")
        if not is_valid_input(name):
            return PressResult(False, f"Unknown key '{name}'")
        if accessibility_allowed() is False:
            return PressResult(False, "not allowed to press keys: turn on Accessibility for this app in System Settings")
        hold = max(0.001, hold_ms / 1000.0)
        pid = None
        if method == DIRECT_METHOD:
            pid = _game_pid(game) or foreground_pid()
            if not pid or pid == os.getpid():
                return PressResult(False, "No game to send to: press the arm hotkey in the game once so the app learns it")
        elif own_window_focused():
            return PressResult(False, "The app itself is in front; click into the game window first")
        if name in MOUSE_BUTTONS:
            events = (make_mouse_event(name, True), make_mouse_event(name, False))
        else:
            events = (make_key_event(name, True), make_key_event(name, False))
        if None in events:
            return PressResult(False, f"Could not create the input for '{name}'")
        for i, event in enumerate(events):
            if pid:
                Quartz.CGEventPostToPid(pid, event)
            else:
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            if i == 0:
                # Games read input once per frame, so the key must stay down
                # long enough for at least one frame to see it.
                time.sleep(hold)
        return PressResult(True)


class KeyWatcher:
    """Edge-detects presses of a key or mouse button by polling its state."""

    def __init__(self) -> None:
        self._was_down = False

    def pressed(self, name: str) -> bool:
        if Quartz is None:
            return False
        state = Quartz.kCGEventSourceStateCombinedSessionState
        if name in KEY_NAMES:
            down = bool(Quartz.CGEventSourceKeyState(state, KEY_NAMES[name]))
        elif name in MOUSE_BUTTONS:
            down = bool(Quartz.CGEventSourceButtonState(state, MOUSE_BUTTONS[name][2]))
        else:
            return False
        fired = down and not self._was_down
        self._was_down = down
        return fired


# --------------------------------------------------------------------------
# Sounds
# --------------------------------------------------------------------------

_SOUNDS = {
    "arm": "Tink",
    "disarm": "Pop",
    "strike": "Morse",
    "done": "Glass",
}


def play_sound(kind: str) -> None:
    """Play a system sound without waiting for it (never blocks capture)."""
    name = _SOUNDS.get(kind)
    if not IS_MAC or not name:
        return
    with contextlib.suppress(OSError):
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


# --------------------------------------------------------------------------
# App window helpers used by the UI
# --------------------------------------------------------------------------


def hide_app() -> None:
    """Hide this app so the app behind it (normally the game) comes to the
    front, like minimizing the window does on Windows."""
    if AppKit is not None:
        with contextlib.suppress(Exception):
            AppKit.NSApplication.sharedApplication().hide_(None)


def show_app() -> None:
    """Bring this app back to the front after hide_app()."""
    if AppKit is not None:
        with contextlib.suppress(Exception):
            nsapp = AppKit.NSApplication.sharedApplication()
            nsapp.unhide_(None)
            nsapp.activateIgnoringOtherApps_(True)
