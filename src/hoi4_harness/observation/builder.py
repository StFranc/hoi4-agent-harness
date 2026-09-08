"""Assemble the Observation the agent actually receives."""

from __future__ import annotations

from ..types import GameState, Observation
from .delta import delta_is_large, render_delta
from .render import render_full


class ObservationBuilder:
    """Decides, per turn, whether to send a full brief or a delta.

    Full brief when: it is the first turn, `full_brief_every` turns have passed,
    the diff has grown large, or a critical event fired (on a critical turn the
    model needs the whole picture, not a diff against a world that just changed).
    """

    def __init__(self, full_brief_every: int = 8):
        self.full_brief_every = max(1, full_brief_every)
        self._previous: GameState | None = None
        self._turns_since_full = 0

    def build(
        self,
        state: GameState,
        turn: int,
        legal_actions: list[str] | None = None,
        notes: list[str] | None = None,
        *,
        remember: bool = True,
    ) -> Observation:
        critical = any(e.severity == "critical" for e in state.events)
        use_full = (
            self._previous is None
            or critical
            or self._turns_since_full >= self.full_brief_every - 1
            or delta_is_large(self._previous, state)
        )

        if use_full:
            brief = render_full(state)
        else:
            brief = render_delta(self._previous, state)

        # remember=False renders a look that must not become the diff baseline,
        # so the next build still qualifies for a full brief (env.reset uses it).
        if remember:
            self._turns_since_full = 0 if use_full else self._turns_since_full + 1
            self._previous = _snapshot(state)
        return Observation(
            turn=turn,
            state=state,
            brief=brief,
            is_delta=not use_full,
            legal_actions=legal_actions or [],
            notes=notes or [],
        )


def _snapshot(state: GameState) -> GameState:
    """Copy enough of the state to diff against next turn.

    A shallow copy would alias the adapter's own mutable lists, and the mock
    mutates them in place -- the diff would then always be empty.
    """
    import copy

    clone = copy.deepcopy(state)
    clone.raw = {}
    return clone
