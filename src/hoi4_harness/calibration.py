"""Calibration: capture and load the mouse coordinates the input driver clicks.

A hotkey keeps working when the resolution or UI scale changes; a pixel
coordinate does not. So coordinates live in a file an operator writes by aiming
the real mouse at each target, never in code, and the file records the screen
resolution it was captured at.

That record is load-bearing, not metadata. Coordinates captured at one
resolution land on different pixels at another, and clicking whatever now
happens to live at the old pixel is exactly the silent failure this harness
refuses elsewhere -- so a calibration loaded for use at another resolution is
refused with both resolutions named, not applied with a warning.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Bumped when the file layout changes. A loader refuses other versions rather
# than misreading a file it does not understand, like the LLM Bridge does.
SCHEMA_VERSION = 1

DEFAULT_FILENAME = "calibration.json"

# What `hoi4-harness calibrate` walks through when the operator does not name
# targets. The panels the hotkeys already open, since the clicks that need
# calibrating are the ones *inside* them that no hotkey reaches. Each UI script
# the input driver grows will name what it needs; this list grows with it.
DEFAULT_TARGETS = (
    "national_focus",
    "research",
    "production",
    "construction",
    "diplomacy",
    "trade",
)


@dataclass
class Calibration:
    """Named click targets, plus the resolution they were captured at."""

    screen: tuple[int, int]
    coordinates: dict[str, tuple[int, int]]


def _gui():
    """The pyautogui module, imported on first use (see InputDriverAdapter._gui)."""
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pip install 'hoi4-agent-harness[input]' for calibration") from exc
    return pyautogui


def screen_size() -> tuple[int, int]:
    """The current screen resolution, in pixels."""
    size = _gui().size()
    return int(size.width), int(size.height)


def mouse_position() -> tuple[int, int]:
    """Where the operator's mouse is right now, in pixels."""
    point = _gui().position()
    return int(point.x), int(point.y)


def capture_calibration(
    targets: Sequence[str] = DEFAULT_TARGETS,
    *,
    input_fn: Callable[[str], str] = input,
    screen_size_fn: Callable[[], tuple[int, int]] = screen_size,
    mouse_position_fn: Callable[[], tuple[int, int]] = mouse_position,
) -> Calibration:
    """Walk the operator through recording each target, returning what was recorded.

    The pause-for-the-operator step is ``input_fn`` (``input`` by default) and
    the screen is read through ``screen_size_fn``/``mouse_position_fn``, so a
    test can drive the whole walk with fakes and no keyboard, mouse, or pyautogui.
    """
    print(
        "Calibration records where your mouse is when you press Enter, so have the game "
        "open, focused, and at the resolution you actually play at.\n"
        f"Targets: {', '.join(targets)}"
    )
    resolution = screen_size_fn()
    coordinates: dict[str, tuple[int, int]] = {}
    for target in targets:
        input_fn(f"Aim the mouse at {target!r}, then press Enter: ")
        # Read the position instead of waiting for a click: a click would act on
        # the live game, and aiming is the part that carries the information.
        coordinates[target] = mouse_position_fn()
        print(f"  {target} -> {coordinates[target][0]}, {coordinates[target][1]}")
    return Calibration(screen=resolution, coordinates=coordinates)


def save_calibration(calibration: Calibration, path: Path) -> Path:
    """Write a calibration as JSON, creating parent directories; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "screen": list(calibration.screen),
                "coordinates": {
                    name: list(point) for name, point in calibration.coordinates.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_calibration(
    path: Path, current_resolution: tuple[int, int] | None = None
) -> Calibration:
    """Read a calibration file, refusing one captured at another resolution.

    Passing ``current_resolution`` (the screen the coordinates will be used on)
    turns the recorded resolution into a staleness check; ``None`` just parses
    the file, for inspection or a dry run.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No calibration file at {path}. Run: hoi4-harness calibrate")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Calibration file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Calibration file {path} is not a calibration object.")
    if data.get("version") != SCHEMA_VERSION:
        raise ValueError(
            f"Calibration file {path} has schema version {data.get('version')!r} but this "
            f"harness speaks v{SCHEMA_VERSION}. Re-run: hoi4-harness calibrate"
        )
    calibration = Calibration(
        screen=_point(data.get("screen"), f"{path}: screen"),
        coordinates={
            name: _point(point, f"{path}: target {name!r}")
            for name, point in (data.get("coordinates") or {}).items()
        },
    )
    if current_resolution is not None and calibration.screen != current_resolution:
        raise RuntimeError(
            f"Calibration was captured at {_fmt(calibration.screen)} but this screen is "
            f"{_fmt(current_resolution)}; coordinates from another resolution click whatever "
            "happens to be at those pixels now. Re-run: hoi4-harness calibrate"
        )
    return calibration


def _point(raw: object, where: str) -> tuple[int, int]:
    """Coerce a stored [x, y] into a tuple of ints, naming the file it came from."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{where} is not an (x, y) pair: {raw!r}")
    return int(raw[0]), int(raw[1])


def _fmt(resolution: tuple[int, int]) -> str:
    return f"{resolution[0]}x{resolution[1]}"
