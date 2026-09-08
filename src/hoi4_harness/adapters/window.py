"""Which window is actually focused.

Synthetic input goes wherever the focus is. If the game loses focus -- you
alt-tab, a launcher steals it, an update popup appears -- the harness types army
hotkeys into a browser or an editor. ``pyautogui.FAILSAFE`` catches a runaway
mouse; nothing catches misdirected keystrokes.

So input is gated on the focused window's title. The rule this module exists to
enforce: **an unimplemented platform reports "unsupported", never "fine"**. A
guard that always passes is worse than no guard, because it is trusted.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


class WindowCheckUnsupported(RuntimeError):
    """No way to read the focused window on this platform."""


@dataclass
class FocusCheck:
    focused: bool
    title: str | None
    supported: bool = True
    reason: str = ""


def active_window_title() -> str | None:
    """Title of the focused window, or None if there isn't one.

    Raises :class:`WindowCheckUnsupported` where it cannot be determined.
    """
    if sys.platform == "win32":
        return _windows_title()
    if sys.platform == "darwin":
        return _macos_title()
    if sys.platform.startswith("linux"):
        return _linux_title()
    raise WindowCheckUnsupported(f"no focused-window check for {sys.platform}")


def _windows_title() -> str | None:
    import ctypes

    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value or None


def _macos_title() -> str | None:
    script = (
        'tell application "System Events" to get name of first application process '
        "whose frontmost is true"
    )
    try:
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowCheckUnsupported(f"osascript unavailable: {exc}") from exc
    if out.returncode != 0:
        raise WindowCheckUnsupported(f"osascript failed: {out.stderr.strip()}")
    return out.stdout.strip() or None


def _linux_title() -> str | None:
    for command in (["xdotool", "getactivewindow", "getwindowname"], ["xprop", "-root"]):
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and command[0] == "xdotool":
            return out.stdout.strip() or None
    raise WindowCheckUnsupported(
        "install xdotool for the focused-window check, or pass --no-window-guard "
        "if you accept that input may go to the wrong window"
    )


def check_focus(expected_title: str) -> FocusCheck:
    """Is a window whose title contains ``expected_title`` focused?

    Matching is a case-insensitive substring, because the real title carries the
    version ("Hearts of Iron IV v1.16.4") and a launcher may add its own suffix.
    """
    try:
        title = active_window_title()
    except WindowCheckUnsupported as exc:
        return FocusCheck(focused=False, title=None, supported=False, reason=str(exc))

    if title is None:
        return FocusCheck(False, None, reason="no window is focused")
    if expected_title.strip().lower() in title.lower():
        return FocusCheck(True, title)
    return FocusCheck(False, title, reason=f"focused window is {title!r}")
