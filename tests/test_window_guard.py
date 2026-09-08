"""The input driver must not type into the wrong window.

Every check here uses an injected focus function. Nothing in this file can send a
keystroke, which is the point: a test suite for an input driver should not be
able to drive the machine it runs on.
"""

from __future__ import annotations

import sys

import pytest

from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, NotFocused
from hoi4_harness.adapters.window import FocusCheck, WindowCheckUnsupported, check_focus
from hoi4_harness.types import ActionCall


def focused(title="Hearts of Iron IV (OpenGL)"):
    return lambda expected: FocusCheck(True, title)


def elsewhere(title="Firefox"):
    return lambda expected: FocusCheck(False, title, reason=f"focused window is {title!r}")


def unsupported(reason="no focused-window check for aix"):
    return lambda expected: FocusCheck(False, None, supported=False, reason=reason)


def driver(check, dry_run=False, **config):
    return InputDriverAdapter(InputConfig(**config), dry_run=dry_run, focus_check=check)


# --- title matching ---------------------------------------------------------

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Hearts of Iron IV (OpenGL)", True),
        ("Hearts of Iron IV v1.16.4", True),
        ("hearts of iron iv", True),
        ("Hearts of Iron IV Launcher", True),
        ("Firefox", False),
        ("Notepad - hoi4-notes.txt", False),
    ],
)
def test_substring_matching_is_case_insensitive(monkeypatch, title, expected):
    monkeypatch.setattr("hoi4_harness.adapters.window.active_window_title", lambda: title)
    assert check_focus("Hearts of Iron IV").focused is expected


def test_no_focused_window_is_not_focused(monkeypatch):
    monkeypatch.setattr("hoi4_harness.adapters.window.active_window_title", lambda: None)
    result = check_focus("Hearts of Iron IV")
    assert not result.focused and "no window" in result.reason


def test_an_unsupported_platform_reports_unsupported_rather_than_fine(monkeypatch):
    def boom():
        raise WindowCheckUnsupported("no check for plan9")

    monkeypatch.setattr("hoi4_harness.adapters.window.active_window_title", boom)
    result = check_focus("Hearts of Iron IV")
    assert not result.focused and not result.supported


@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 path needs Windows")
def test_the_windows_implementation_returns_a_title():
    from hoi4_harness.adapters.window import active_window_title

    title = active_window_title()
    assert title is None or isinstance(title, str)


# --- the driver -------------------------------------------------------------

def test_input_is_refused_when_another_window_is_focused():
    with pytest.raises(NotFocused, match="Firefox"):
        driver(elsewhere()).press("space")


def test_an_unverifiable_platform_refuses_rather_than_sending_blind():
    with pytest.raises(NotFocused, match="cannot verify"):
        driver(unsupported()).press("space")


def test_clicking_is_gated_too():
    with pytest.raises(NotFocused):
        driver(elsewhere(), coordinates={"x": (10, 10)}).click("x")


def test_dry_run_never_checks_because_nothing_is_sent():
    adapter = driver(elsewhere(), dry_run=True)
    adapter.press("space")                    # must not raise
    assert adapter.log == ["press space"]


def test_the_guard_can_be_switched_off_deliberately():
    adapter = driver(unsupported(), enforce_focus=False)
    adapter.log.append("sentinel")
    # No exception from the guard; the only failure left is the missing SDK.
    with pytest.raises(RuntimeError, match="pip install"):
        adapter.press("space")


def test_a_misdirected_action_is_a_failed_result_not_a_crash():
    """The agent has to be able to read this and wait, like any other rejection."""
    result = driver(elsewhere()).apply(ActionCall("set_game_speed", {"speed": 3}))
    assert not result.ok
    assert result.error_kind == "rejected"
    assert "not focused" in result.message


def test_note_still_works_when_the_game_is_not_focused():
    """It writes to the journal; it sends no input, so the guard is irrelevant."""
    assert driver(elsewhere()).apply(ActionCall("note", {"text": "hold"})).ok


def test_the_focused_case_passes_the_guard_and_reaches_the_input_layer():
    with pytest.raises(RuntimeError, match="pip install"):
        driver(focused()).press("space")
