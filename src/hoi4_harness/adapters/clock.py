"""Waiting for the game clock.

Two adapters need the same thing -- run the game forward N in-game days, stop
early if something critical happens -- and neither should reimplement it. The
wall-clock deadline is a backstop, not the mechanism: it exists so a wedged game
or a stalled log ends the turn instead of hanging the run forever.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

from ..types import GameState

#: Seconds of wall clock allowed per in-game day before giving up on a turn.
#: Generous: at speed 5 a day is well under a second, and a paused game that
#: nobody unpauses should still eventually return control.
SECONDS_PER_GAME_DAY = 20.0
MIN_DEADLINE_SECONDS = 30.0


def days_between(start_iso: str, end_iso: str) -> int:
    """In-game days from one ISO date to another. 0 if either is unparseable."""
    try:
        return (date.fromisoformat(end_iso) - date.fromisoformat(start_iso)).days
    except (ValueError, TypeError):
        return 0


def has_critical_event(state: GameState) -> bool:
    return any(event.severity == "critical" for event in state.events)


def wait_for_days(
    read_state: Callable[[], GameState],
    days: int,
    poll_seconds: float = 0.5,
    seconds_per_day: float = SECONDS_PER_GAME_DAY,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> GameState:
    """Poll ``read_state`` until the in-game date has moved ``days`` forward.

    Returns as soon as a critical event appears -- the whole point of a wake rule
    is that a war declaration does not wait for the schedule. ``sleep`` and
    ``now`` are injectable so tests do not spend real time.
    """
    state = read_state()
    start = state.date
    if days <= 0 or has_critical_event(state):
        return state

    deadline = now() + max(MIN_DEADLINE_SECONDS, days * seconds_per_day)
    while now() < deadline:
        if has_critical_event(state) or days_between(start, state.date) >= days:
            return state
        sleep(poll_seconds)
        state = read_state()
    return state
