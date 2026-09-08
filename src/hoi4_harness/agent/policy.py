"""The cheap layer: decide whether this moment is worth a model call at all.

HOI4 runs in real time and most of that time is empty. A loop that asks a model
"what now?" every game-day would spend a fortune to answer "keep building" nine
hundred times. So the harness splits control in two:

* **Reflex** -- deterministic Python that handles the obvious and the urgent
  (queue empty, factories idle, pause when something explodes). Free, instant.
* **Planner** -- the LLM, woken only at decision points: a focus finished, a
  research slot opened, a war started, a front broke, or the scheduled review
  came round.

The wake rule is the whole cost model of this project. Widening it is how a run
gets expensive; the defaults below put a 1936-1939 campaign in the low hundreds
of model calls.

There is deliberately no third layer between these two. A cheap model triaging
whether the planner is worth waking has to read the same brief to judge, so it
pays nearly the same input cost -- and input is where the money goes. Measured on
a live 12-turn run: 39,294 input tokens against 2,698 output. Prompt caching has
already made a planner wake cheap; a gatekeeper in front of it would save a
fraction of 6% of the bill, in exchange for a second provider surface and a
second, invisible wake rule. If that trade ever looks worth making, measure it
first -- the numbers above are how.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import ActionCall, GameState


@dataclass
class WakeDecision:
    wake: bool
    reason: str
    urgency: str = "scheduled"   # scheduled | opportunity | critical


class Policy:
    """Reflex behaviour plus the wake rule."""

    def __init__(
        self,
        days_per_turn: int = 7,
        wake_on_no_focus: bool = True,
        wake_on_free_research_slot: bool = True,
        reflex_enabled: bool = True,
        planner_enabled: bool = True,
    ):
        self.days_per_turn = days_per_turn
        self.wake_on_no_focus = wake_on_no_focus
        self.wake_on_free_research_slot = wake_on_free_research_slot
        self.reflex_enabled = reflex_enabled
        self.planner_enabled = planner_enabled

    # --- when to spend a model call -----------------------------------------

    def should_wake(self, state: GameState, days_since_planner: int) -> WakeDecision:
        if not self.planner_enabled:
            # The reflex-only baseline: nothing is worth a model call, however
            # loud the game gets, because there is no model to wake.
            return WakeDecision(False, "planner disabled: reflex-only run")

        for event in state.events:
            if event.severity == "critical":
                return WakeDecision(True, f"critical event: {event.text}", "critical")

        if self.wake_on_no_focus and not state.national_focus and state.known("national_focus"):
            return WakeDecision(True, "no national focus is running", "opportunity")

        free_slots = sum(1 for slot in state.research if slot.technology is None)
        if self.wake_on_free_research_slot and free_slots and state.known("research"):
            return WakeDecision(True, f"{free_slots} research slot(s) idle", "opportunity")

        if state.fronts and any(f.pressure == "losing_ground" for f in state.fronts):
            return WakeDecision(True, "a front is losing ground", "critical")

        if days_since_planner >= self.days_per_turn:
            return WakeDecision(True, "scheduled review", "scheduled")

        return WakeDecision(False, "nothing worth a decision")

    # --- what to do without a model -----------------------------------------

    def reflex_actions(self, state: GameState) -> list[ActionCall]:
        """Deterministic housekeeping. Only obvious, reversible things belong here.

        Anything requiring a judgement call -- what to build, whom to fight -- is
        the planner's job, and stays out of this list even when a heuristic would
        usually be right.
        """
        actions: list[ActionCall] = []
        if not self.reflex_enabled:
            return actions

        if state.known("construction") and not state.construction and state.civilian_factories:
            actions.append(
                ActionCall(
                    name="queue_construction",
                    arguments={"building": "civilian_factory", "state": "capital", "count": 2},
                    rationale="reflex: construction queue was empty",
                )
            )

        if state.known("military_factories") and state.production:
            assigned = sum(line.factories for line in state.production)
            idle = state.military_factories - assigned
            if idle > 0:
                main = max(state.production, key=lambda line: line.factories)
                actions.append(
                    ActionCall(
                        name="set_production",
                        arguments={"equipment": main.equipment, "factories": main.factories + idle},
                        rationale=f"reflex: {idle} military factories were idle",
                    )
                )

        return actions
