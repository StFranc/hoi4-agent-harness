"""The adapter contract: everything the harness needs from "a running game".

HOI4 ships no API. Every real adapter is therefore an approximation, and each one
sees a different subset of the world. The contract below is written so an adapter
can be honest about that: ``capabilities()`` says which actions it can actually
perform, and ``read_state()`` marks what it could not observe instead of
inventing a zero.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable
from dataclasses import dataclass

from ..types import ActionCall, ActionResult, GameEvent, GameState


@dataclass
class AdapterInfo:
    name: str
    readable: bool          # can it observe state?
    writable: bool          # can it perform actions?
    clock_control: bool     # can it pause/unpause/set speed?
    notes: str = ""


class GameAdapter(abc.ABC):
    """A bidirectional bridge to one running game."""

    #: Action names from ``actions.catalog`` this adapter can execute.
    supported_actions: frozenset[str] = frozenset()

    @abc.abstractmethod
    def info(self) -> AdapterInfo: ...

    @abc.abstractmethod
    def read_state(self) -> GameState:
        """Return the current snapshot. Must not block on the game clock.

        Must not consume events either. A turn reads state several times -- the
        clock wait polls, the brief, verification, the reflexes -- and only one
        of those reads is the one the wake rule judges. An adapter that queues
        events keeps each one in ``events`` until the loop passes it to
        ``acknowledge_events``.
        """

    def acknowledge_events(self, events: Iterable[GameEvent]) -> None:
        """The loop has answered these events: stop reporting them.

        Called once a turn, before the clock moves on, with the very event
        objects the turn's reads returned -- the ones the wake rule judged and
        the brief showed. An event that arrived after those reads is not among
        them and stays for the next turn. Default: nothing to do, for adapters
        that do not queue events.
        """

    @abc.abstractmethod
    def apply(self, call: ActionCall) -> ActionResult:
        """Execute one validated action."""

    # --- clock ---------------------------------------------------------------
    # The harness, not the model, owns time. The standard cycle is
    # pause -> read -> decide -> apply -> advance.

    def pause(self) -> None:
        """Pause the game. Default: no-op for adapters without clock control."""

    def resume(self, speed: int = 3) -> None:
        """Resume at ``speed`` (1-5)."""

    @abc.abstractmethod
    def advance(self, days: int) -> GameState:
        """Run the game forward up to ``days`` in-game days, then pause again.

        Implementations must return early when a ``critical`` event fires, so the
        planner can be woken before the scheduled tick.
        """

    def close(self) -> None:
        """Release windows, file handles, subprocesses."""

    # --- helpers -------------------------------------------------------------

    def unsupported(self, call: ActionCall) -> ActionResult:
        return ActionResult(
            ok=False,
            action=call.name,
            call_id=call.call_id,
            message=(
                f"{self.info().name} adapter cannot perform '{call.name}'. "
                "Supported here: " + ", ".join(sorted(self.supported_actions))
            ),
            error_kind="unsupported",
        )
