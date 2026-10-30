"""Windows input and system helpers (SendInput, hotkeys, focus, sounds).

Everything here degrades to harmless no-ops on other platforms so the rest of
the app can be imported and tested anywhere.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------
# Key names
# --------------------------------------------------------------------------

KEY_NAMES: dict[str, int] = {}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    KEY_NAMES[_c] = ord(_c)
for _i in range(1, 25):
    KEY_NAMES[f"F{_i}"] = 0x6F + _i
KEY_NAMES.update(
    {
        "Space": 0x20,
        "Enter": 0x0D,
        "Tab": 0x09,
        "Backspace": 0x08,
        "Escape": 0x1B,
        "Shift": 0x10,
        "Ctrl": 0x11,
        "Alt": 0x12,
        "Left Shift": 0xA0,
        "Right Shift": 0xA1,
        "Left Ctrl": 0xA2,
        "Right Ctrl": 0xA3,
        "Left Alt": 0xA4,
        "Right Alt": 0xA5,
        "Caps Lock": 0x14,
        "Pause": 0x13,
        "Scroll Lock": 0x91,
        "Print Screen": 0x2C,
        "Insert": 0x2D,
        "Delete": 0x2E,
        "Home": 0x24,
        "End": 0x23,
        "Page Up": 0x21,
        "Page Down": 0x22,
        "Left": 0x25,
        "Up": 0x26,
        "Right": 0x27,
        "Down": 0x28,
        "Num Lock": 0x90,
        "Numpad *": 0x6A,
        "Numpad +": 0x6B,
        "Numpad -": 0x6D,
        "Numpad .": 0x6E,
        "Numpad /": 0x6F,
        "`": 0xC0,
        "-": 0xBD,
        "=": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        "\\": 0xDC,
        ";": 0xBA,
        "'": 0xDE,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
    }
)
for _i in range(10):
    KEY_NAMES[f"Numpad {_i}"] = 0x60 + _i

# name -> (down flag, up flag, mouseData, virtual key for polling)
MOUSE_BUTTONS: dict[str, tuple[int, int, int, int]] = {
    "Mouse Left": (0x0002, 0x0004, 0, 0x01),
    "Mouse Right": (0x0008, 0x0010, 0, 0x02),
    "Mouse Middle": (0x0020, 0x0040, 0, 0x04),
    "Mouse X1": (0x0080, 0x0100, 1, 0x05),
    "Mouse X2": (0x0080, 0x0100, 2, 0x06),
}

_VK_TO_NAME = {vk: name for name, vk in KEY_NAMES.items()}

# Tk keysyms (used when detecting a key off Windows) that differ from our names.
_KEYSYM_ALIASES = {
    "space": "Space",
    "Return": "Enter",
    "BackSpace": "Backspace",
    "Shift_L": "Left Shift",
    "Shift_R": "Right Shift",
    "Control_L": "Left Ctrl",
    "Control_R": "Right Ctrl",
    "Alt_L": "Left Alt",
    "Alt_R": "Right Alt",
    "Prior": "Page Up",
    "Next": "Page Down",
    "Caps_Lock": "Caps Lock",
    "Scroll_Lock": "Scroll Lock",
    "Num_Lock": "Num Lock",
    "Print": "Print Screen",
}


def all_input_names() -> list[str]:
    return list(KEY_NAMES) + list(MOUSE_BUTTONS)


def is_valid_input(name: str) -> bool:
    return name in KEY_NAMES or name in MOUSE_BUTTONS


def vk_for(name: str) -> int | None:
    if name in KEY_NAMES:
        return KEY_NAMES[name]
    if name in MOUSE_BUTTONS:
        return MOUSE_BUTTONS[name][3]
    return None


def name_from_tk_event(keycode: int, keysym: str) -> str | None:
    """Map a Tk key event to one of our key names.

    On Windows Tk reports the virtual-key code as ``keycode``.
    """
    if IS_WINDOWS and keycode in _VK_TO_NAME:
        return _VK_TO_NAME[keycode]
    if keysym in _KEYSYM_ALIASES:
        return _KEYSYM_ALIASES[keysym]
    if len(keysym) == 1 and keysym.upper() in KEY_NAMES:
        return keysym.upper()
    if keysym in KEY_NAMES:
        return keysym
    return None


# --------------------------------------------------------------------------
# Win32 bindings
# --------------------------------------------------------------------------

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC_EX = 4

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )

    def _send(*inputs: "INPUT") -> int:
        arr = (INPUT * len(inputs))(*inputs)
        return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def _key_input(vk: int, up: bool, scan: bool = True) -> "INPUT":
        sc = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX)
        flags = KEYEVENTF_SCANCODE if scan else 0
        if sc & 0xFF00 in (0xE000, 0xE100):
            flags |= KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        # Scan codes are what DirectInput / raw-input games listen to; Windows
        # derives the matching virtual key for apps that read window messages.
        inp.ki = KEYBDINPUT(wVk=vk, wScan=sc & 0xFF, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    def _key_lparam(vk: int, up: bool) -> int:
        sc = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX)
        lp = 1 | ((sc & 0xFF) << 16)
        if sc & 0xFF00 in (0xE000, 0xE100):
            lp |= 1 << 24
        if up:
            lp |= (1 << 30) | (1 << 31)
        return lp

    def _mouse_input(flag: int, data: int) -> "INPUT":
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flag, time=0, dwExtraInfo=0)
        return inp


# --------------------------------------------------------------------------
# Public helpers
# --------------------------------------------------------------------------


@dataclass
class PressResult:
    ok: bool
    message: str = ""


def foreground_pid() -> int | None:
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def foreground_title() -> str:
    if not IS_WINDOWS:
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def own_window_focused() -> bool:
    return foreground_pid() == os.getpid()


# Window classes of the desktop and taskbar.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _exe_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def foreground_app() -> str | None:
    """Program file name (e.g. "Game.exe") of the window that has focus.

    None when that is this app, the desktop or the taskbar, or no window;
    "" when the name can't be read.
    """
    if not IS_WINDOWS:
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in _SHELL_CLASSES:
        return None
    return _exe_name(pid.value)


def game_in_front(game: str = "") -> tuple[bool, str]:
    """Whether the game's window has focus (windowed, borderless or fullscreen).

    ``game`` is the game's program file name; when empty, any other app
    than this one, the desktop and the taskbar counts. Returns (ok, reason
    when not).
    """
    if not IS_WINDOWS:
        return True, ""
    app = foreground_app()
    if app is None:
        return False, "this app or the desktop has focus" if foreground_pid() else "no window has focus"
    if game and app and app.lower() != game.lower():
        return False, f"{app} has focus, not {game}"
    return True, ""


def find_game_window(game: str) -> int | None:
    """The game's main window: the largest visible top-level window of the
    program named ``game``, or None."""
    if not IS_WINDOWS or not game:
        return None
    found: list[tuple[int, int]] = []
    names: dict[int, str] = {}

    @WNDENUMPROC
    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER: skip popups
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in names:
                names[pid.value] = _exe_name(pid.value).lower()
            if names[pid.value] == game.lower():
                r = _window_rect(hwnd)
                found.append(((r[2] - r[0]) * (r[3] - r[1]), hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return max(found)[1] if found else None


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Visible bounds of a window (without the invisible resize borders)."""
    rect = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:  # extended frame
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def game_visible(game: str = "") -> tuple[bool, str]:
    """Whether the game's window can be seen, focused or not: running, not
    minimized, and not covered by the window that has focus.

    Without a known ``game`` this falls back to game_in_front().
    """
    if not IS_WINDOWS:
        return True, ""
    if not game:
        return game_in_front(game)
    app = foreground_app()
    if app and app.lower() == game.lower():
        return True, ""
    hwnd = find_game_window(game)
    if not hwnd:
        return False, f"{game} isn't running"
    if user32.IsIconic(hwnd):
        return False, f"{game} is minimized"
    fg = user32.GetForegroundWindow()
    cls = ctypes.create_unicode_buffer(64)
    if fg:
        user32.GetClassNameW(fg, cls, 64)
    if fg and cls.value not in _SHELL_CLASSES and not user32.IsIconic(fg):
        a, b = _window_rect(fg), _window_rect(hwnd)
        if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
            return False, f"{app or 'this app'} is in front of {game}"
    return True, ""


def _bring_to_front(hwnd: int) -> bool:
    """Give ``hwnd`` the focus, working around Windows' focus-stealing rules."""
    if user32.GetForegroundWindow() == hwnd:
        return True
    # Windows lets the process that sent the last input change the focus,
    # so send a mouse "move" by zero first.
    _send(_mouse_input(0x0001, 0))  # MOUSEEVENTF_MOVE
    user32.BringWindowToTop(hwnd)
    if not user32.SetForegroundWindow(hwnd):
        # Fall back to sharing input state with the focused window's thread.
        fg = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        if theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(mine, theirs, False)
    return user32.GetForegroundWindow() == hwnd


# Time the game gets to notice it has focus before the key goes down, and
# to read the last key release before focus goes back.
FOCUS_SETTLE_S = 0.1


@contextlib.contextmanager
def game_focus(game: str):
    """Within the block the game has focus; afterwards focus goes back to
    the window that had it. Does nothing without a ``game``, when the game
    already has focus, or off Windows. Yields a message if switching failed."""
    hwnd = find_game_window(game) if IS_WINDOWS and game else None
    prev = user32.GetForegroundWindow() if hwnd else None
    if not hwnd or prev == hwnd:
        yield ""
        return
    if not _bring_to_front(hwnd):
        yield f"Windows didn't let this app switch to {game}"
        return
    time.sleep(FOCUS_SETTLE_S)
    try:
        yield ""
    finally:
        time.sleep(FOCUS_SETTLE_S)
        if prev and user32.IsWindow(prev):
            _bring_to_front(prev)


def process_elevated(pid: int | None = None) -> bool | None:
    """True if the process runs as administrator, None if it can't be told."""
    if not IS_WINDOWS:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_ELEVATION = 20
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid or os.getpid())
    if not handle:
        # Not even allowed to look: almost always an elevated process.
        return True if pid else None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return True if pid else None
        try:
            elevated = wintypes.DWORD()
            size = wintypes.DWORD()
            ok = advapi32.GetTokenInformation(
                token, TOKEN_ELEVATION, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)
            )
            return bool(elevated.value) if ok else None
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


# Ways of sending the photo key, as shown in the UI.
INPUT_METHODS = ["Scan code", "Virtual key", "Window message"]

# Mouse button -> (down message, up message, wParam) for window messages.
_MOUSE_MESSAGES = {
    "Mouse Left": (0x0201, 0x0202, 0x0001),
    "Mouse Right": (0x0204, 0x0205, 0x0002),
    "Mouse Middle": (0x0207, 0x0208, 0x0010),
    "Mouse X1": (0x020B, 0x020C, 0x00010020),
    "Mouse X2": (0x020B, 0x020C, 0x00020040),
}

ADMIN_HINT = (
    "{app} seems to run as administrator, and then Windows silently drops key presses from this app. "
    "If photos aren't taken, close this app and start it with right-click > Run as administrator."
)


def blocked_by_admin() -> str:
    """A warning when the window in front seems to run as administrator and
    this app doesn't (Windows then drops our input without an error), else ""."""
    if not IS_WINDOWS:
        return ""
    pid = foreground_pid()
    if not pid or pid == os.getpid() or process_elevated() or not process_elevated(pid):
        return ""
    return ADMIN_HINT.format(app=_exe_name(pid) or "The game")


# Marks the copy started by relaunch_as_admin, so it never relaunches again.
RELAUNCHED_FLAG = "--relaunched"


def relaunch_as_admin() -> bool:
    """Start this app again as administrator; Windows asks the user first.

    Returns True if the new copy started (the caller should then exit).
    """
    if not IS_WINDOWS:
        return False
    import subprocess

    args = [a for a in sys.argv[1:] if a != RELAUNCHED_FLAG] + [RELAUNCHED_FLAG]
    if not getattr(sys, "frozen", False):
        args.insert(0, os.path.abspath(sys.argv[0]))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = (
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    )
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    # Values above 32 mean success; the user saying No to the prompt is an error.
    result = shell32.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(args), None, 1)
    return (result or 0) > 32


class InputSender:
    """Presses the shutter key or mouse button in whatever window has focus."""

    def press(self, name: str, hold_ms: int, method: str = INPUT_METHODS[0], game: str = "") -> PressResult:
        """Press in the focused window, or with "Window message" in ``game``'s
        window when it is known (it then needs no focus)."""
        if not IS_WINDOWS:
            return PressResult(False, "Sending input is only supported on Windows")
        hold = max(0.001, hold_ms / 1000.0)
        if name not in MOUSE_BUTTONS and name not in KEY_NAMES:
            return PressResult(False, f"Unknown key '{name}'")
        if method == "Window message":
            return self._post(name, hold, find_game_window(game) or user32.GetForegroundWindow())
        if own_window_focused():
            return PressResult(False, "The app itself has focus; click into the game window first")
        if name in MOUSE_BUTTONS:
            down, up, data, _ = MOUSE_BUTTONS[name]
            sent = _send(_mouse_input(down, data))
            time.sleep(hold)
            sent += _send(_mouse_input(up, data))
        else:
            vk = KEY_NAMES[name]
            scan = method != "Virtual key"
            sent = _send(_key_input(vk, up=False, scan=scan))
            # Games read input once per frame, so the key must stay down
            # long enough for at least one frame to see it.
            time.sleep(hold)
            sent += _send(_key_input(vk, up=True, scan=scan))
        if sent != 2:
            err = ctypes.get_last_error()
            return PressResult(False, f"Windows rejected the input (error {err})")
        return PressResult(True)

    def _post(self, name: str, hold: float, hwnd: int | None) -> PressResult:
        """Post the press straight to a window's message queue."""
        if not hwnd:
            return PressResult(False, "No window to send to")
        if hwnd == user32.GetForegroundWindow() and own_window_focused():
            return PressResult(False, "The app itself has focus; click into the game window first")
        if name in MOUSE_BUTTONS:
            down, up, wparam = _MOUSE_MESSAGES[name]
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            lparam = ((rect.bottom // 2) << 16) | (rect.right // 2)
            msgs = ((down, wparam, lparam), (up, wparam & ~0xFFFF, lparam))
        else:
            vk = KEY_NAMES[name]
            msgs = ((0x0100, vk, _key_lparam(vk, False)), (0x0101, vk, _key_lparam(vk, True)))  # WM_KEYDOWN/UP
        for i, (msg, wparam, lparam) in enumerate(msgs):
            if not user32.PostMessageW(hwnd, msg, wparam, lparam):
                return PressResult(False, f"Windows rejected the message (error {ctypes.get_last_error()})")
            if i == 0:
                time.sleep(hold)
        return PressResult(True)


class KeyWatcher:
    """Edge-detects presses of a key or mouse button via GetAsyncKeyState."""

    def __init__(self) -> None:
        self._was_down = False

    def pressed(self, name: str) -> bool:
        if not IS_WINDOWS:
            return False
        vk = vk_for(name)
        if vk is None:
            return False
        down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        fired = down and not self._was_down
        self._was_down = down
        return fired


def enable_dpi_awareness() -> None:
    """Use real pixel coordinates so regions line up with captured frames."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def high_resolution_timer(enable: bool) -> None:
    """Ask Windows for 1 ms timer resolution while capturing."""
    if not IS_WINDOWS:
        return
    try:
        winmm = ctypes.windll.winmm
        (winmm.timeBeginPeriod if enable else winmm.timeEndPeriod)(1)
    except (AttributeError, OSError):
        pass


_SOUNDS = {
    "arm": ((880, 80), (1320, 120)),
    "disarm": ((1320, 80), (660, 120)),
    "strike": ((1760, 60),),
    "done": ((660, 100), (880, 100), (1100, 160)),
}


def play_sound(kind: str) -> None:
    """Short beep pattern on a background thread (never blocks capture)."""
    if not IS_WINDOWS or kind not in _SOUNDS:
        return

    def run() -> None:
        import winsound

        for freq, dur in _SOUNDS[kind]:
            try:
                winsound.Beep(freq, dur)
            except RuntimeError:
                return

    threading.Thread(target=run, daemon=True).start()
