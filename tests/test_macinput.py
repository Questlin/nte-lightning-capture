"""The macOS key table, and on a Mac the Quartz events built from it."""

import sys

import pytest

from nte_lightning import macinput
from nte_lightning.config import AppConfig


def test_default_keys_exist():
    cfg = AppConfig()
    assert macinput.is_valid_input(cfg.shutter_key)
    assert macinput.is_valid_input(cfg.arm_hotkey)
    assert "Mouse Left" in macinput.all_input_names()


def test_key_codes_are_unique_except_side_aliases():
    aliases = {"Shift": "Left Shift", "Ctrl": "Left Ctrl", "Alt": "Left Alt"}
    seen = {}
    for name, code in macinput.KEY_NAMES.items():
        if name in aliases:
            assert code == macinput.KEY_NAMES[aliases[name]]
            continue
        assert code not in seen, f"{name} and {seen.get(code)} share key code {code:#x}"
        seen[code] = name


def test_tk_keysyms_map_to_names():
    assert macinput.name_from_tk_event(0, "f") == "F"
    assert macinput.name_from_tk_event(0, "F6") == "F6"
    assert macinput.name_from_tk_event(0, "space") == "Space"
    assert macinput.name_from_tk_event(0, "Option_L") == "Left Alt"
    assert macinput.name_from_tk_event(0, "bracketleft") == "["


@pytest.mark.skipif(sys.platform != "darwin" or macinput.Quartz is None, reason="needs macOS and PyObjC")
def test_quartz_events_carry_the_key_code():
    Quartz = macinput.Quartz
    for name in ("F", "F6", "Space", "Numpad 5"):
        event = macinput.make_key_event(name, True)
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        assert code == macinput.KEY_NAMES[name]
    assert macinput.make_mouse_event("Mouse Left", True) is not None
    assert macinput.make_mouse_event("Mouse X1", False) is not None


@pytest.mark.skipif(sys.platform != "darwin" or macinput.AppKit is None, reason="needs macOS and PyObjC")
def test_focus_checks_run():
    ok, reason = macinput.game_in_front("")
    assert isinstance(ok, bool) and isinstance(reason, str)
    assert macinput.game_visible("No Such Game")[0] is False
    with macinput.game_focus("No Such Game") as problem:
        assert problem == ""


def test_macinput_imports_first_in_a_fresh_process():
    # The self-test imports macinput before winput; that order must work too.
    import subprocess

    code = "import nte_lightning.macinput as m, nte_lightning.winput as w; print(m.DIRECT_METHOD)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
