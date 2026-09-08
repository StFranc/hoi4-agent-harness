"""Write-side sink: perform actions by driving keyboard and mouse.

Status: skeleton. The mechanics (focus the window, move, click, hotkey) are
straightforward. What is not straightforward, and what this file is structured
around, is that a blind click is unverifiable: every action should act, re-read,
and confirm the state actually changed, reporting failure rather than assuming
success.

Hotkeys are far more reliable than coordinates and should be preferred wherever
the game exposes one. Coordinates are resolution- and UI-scale-dependent and
belong in a calibration file, not in code; ``load_calibration`` fills
``InputConfig.coordinates`` from that file and refuses one captured at another
resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..calibration import load_calibration, screen_size
from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter
from .window import FocusCheck, check_focus

HOTKEYS = {
    "pause": "space",
    "speed_up": "add",
    "speed_down": "subtract",
    "production": "b",
    "research": "n",
    "national_focus": "f",
    "diplomacy": "j",
    "trade": "t",
    "construction": "c",
    "division_designer": "u",
}


class NotFocused(RuntimeError):
    """The game was not the focused window, so nothing was sent."""


@dataclass
class InputConfig:
    #: Matched as a case-insensitive substring: the real title carries a suffix
    #: ("Hearts of Iron IV (OpenGL)"), so an exact match never fires.
    window_title: str = "Hearts of Iron IV"
    move_duration: float = 0.08
    settle_seconds: float = 0.35
    coordinates: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Refuse to send input unless the game is focused. Turning this off is an
    #: explicit choice to let keystrokes land wherever they land.
    enforce_focus: bool = True


class InputDriverAdapter(GameAdapter):
    """Applies actions through synthetic input. Reads nothing on its own.

    Compose with a read-capable adapter (savegame or screen) via CompositeAdapter.
    """

    supported_actions = frozenset({"set_game_speed", "advance_time", "note"})

    def __init__(
        self,
        config: InputConfig | None = None,
        dry_run: bool = True,
        focus_check=check_focus,
    ):
        self.config = config or InputConfig()
        self.dry_run = dry_run
        self.log: list[str] = []
        self._focus_check = focus_check

    # --- the guard -----------------------------------------------------------

    def focus(self) -> FocusCheck:
        """Where would input land right now?"""
        return self._focus_check(self.config.window_title)

    def _require_focus(self) -> None:
        """Raise unless it is safe to send input.

        Skipped in dry-run, where nothing is sent. An unsupported platform
        refuses rather than passing: a guard that cannot check must not pretend
        it did.
        """
        if self.dry_run or not self.config.enforce_focus:
            return
        result = self.focus()
        if result.focused:
            return
        if not result.supported:
            raise NotFocused(
                f"cannot verify the focused window ({result.reason}); refusing to send input. "
                "Set enforce_focus=False to send anyway."
            )
        raise NotFocused(f"{self.config.window_title!r} is not focused: {result.reason}")

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="input",
            readable=False,
            writable=True,
            clock_control=True,
            notes=f"dry_run={self.dry_run}; requires the 'input' extra (pyautogui)",
        )

    def _gui(self):
        try:
            import pyautogui
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[input]' for input") from exc
        pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
        return pyautogui

    def press(self, key: str) -> None:
        self._require_focus()
        self.log.append(f"press {key}")
        if not self.dry_run:
            self._gui().press(key)

    def click(self, target: str) -> None:
        self._require_focus()
        point = self.config.coordinates.get(target)
        if point is None:
            raise KeyError(f"No calibrated coordinate for {target!r}. Run: hoi4-harness calibrate")
        self.log.append(f"click {target} at {point}")
        if not self.dry_run:
            gui = self._gui()
            gui.moveTo(point[0], point[1], duration=self.config.move_duration)
            gui.click()

    def load_calibration(
        self, path: Path, *, current_resolution: tuple[int, int] | None = None
    ) -> None:
        """Populate ``config.coordinates`` from a calibration file, refusing a stale one.

        ``current_resolution`` stands in for the real screen -- what tests inject.
        Without it a live run probes the screen (so it needs the 'input' extra to
        load at all), while a dry run skips the probe: it sends no clicks, so a
        stale calibration cannot misfire there.
        """
        resolution = current_resolution
        if resolution is None and not self.dry_run:
            resolution = screen_size()
        calibration = load_calibration(path, current_resolution=resolution)
        self.config.coordinates = dict(calibration.coordinates)

    def read_state(self) -> GameState:
        raise NotImplementedError("Write-only adapter; compose it with a reader.")

    def apply(self, call: ActionCall) -> ActionResult:
        try:
            return self._apply(call)
        except NotFocused as exc:
            # The same contract as every other adapter failure: report it, do not
            # pretend the action happened. The agent can read this and wait.
            return ActionResult(
                ok=False,
                action=call.name,
                call_id=call.call_id,
                message=str(exc),
                error_kind="rejected",
            )

    def _apply(self, call: ActionCall) -> ActionResult:
        if call.name == "note":
            return ActionResult(ok=True, action=call.name, call_id=call.call_id, message="Noted.")
        if call.name == "set_game_speed":
            speed = int(call.arguments["speed"])
            self.press(HOTKEYS["pause"] if speed == 0 else HOTKEYS["speed_up"])
            return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                                message=f"Speed -> {speed}")
        if call.name == "advance_time":
            return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                                message="Clock handed back to the harness.")
        # TODO: one UI script per action, each ending in a verify step. Until an
        # action has one, refusing is the honest answer.
        return self.unsupported(call)

    def pause(self) -> None:
        self.press(HOTKEYS["pause"])

    def resume(self, speed: int = 3) -> None:
        self.press(HOTKEYS["pause"])

    def advance(self, days: int) -> GameState:
        raise NotImplementedError("Compose with a reader to observe the clock.")
