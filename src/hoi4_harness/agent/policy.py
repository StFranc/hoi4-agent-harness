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

from ..observation.fronts import LOW_SUPPLY, is_quiet
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
        wake_on_encirclement: bool = True,
        wake_on_supply_collapse: bool = True,
        wake_on_capital_threat: bool = True,
        wake_on_ally_capitulation: bool = True,
        wake_on_front_quiet: bool = True,
        reflex_delegate: bool = False,
    ):
        self.days_per_turn = days_per_turn
        self.wake_on_no_focus = wake_on_no_focus
        self.wake_on_free_research_slot = wake_on_free_research_slot
        self.wake_on_encirclement = wake_on_encirclement
        self.wake_on_supply_collapse = wake_on_supply_collapse
        self.wake_on_capital_threat = wake_on_capital_threat
        self.wake_on_ally_capitulation = wake_on_ally_capitulation
        self.wake_on_front_quiet = wake_on_front_quiet
        self.reflex_delegate = reflex_delegate
        self.reflex_enabled = reflex_enabled
        self.planner_enabled = planner_enabled

    # --- when to spend a model call -----------------------------------------

    def should_wake(
        self,
        state: GameState,
        days_since_planner: int,
        previous: GameState | None = None,
    ) -> WakeDecision:
        """``previous`` is last turn's snapshot. The wartime rules are about
        *change* -- a pocket opening, supply collapsing on a front that was fine
        -- and waking on a condition rather than its onset would wake every turn
        of a long siege for news the model already has."""
        if not self.planner_enabled:
            # The reflex-only baseline: nothing is worth a model call, however
            # loud the game gets, because there is no model to wake.
            return WakeDecision(False, "planner disabled: reflex-only run")

        for event in state.events:
            if event.severity == "critical":
                return WakeDecision(True, f"critical event: {event.text}", "critical")

        if state.wars:
            urgent = self._wartime_alarm(state, previous)
            if urgent is not None:
                return WakeDecision(True, urgent, "critical")

        if self.wake_on_no_focus and not state.national_focus and state.known("national_focus"):
            return WakeDecision(True, "no national focus is running", "opportunity")

        free_slots = sum(1 for slot in state.research if slot.technology is None)
        if self.wake_on_free_research_slot and free_slots and state.known("research"):
            return WakeDecision(True, f"{free_slots} research slot(s) idle", "opportunity")

        if state.fronts and any(f.pressure == "losing_ground" for f in state.fronts):
            return WakeDecision(True, "a front is losing ground", "critical")

        if state.wars and self.wake_on_front_quiet and previous is not None:
            quiet = _went_quiet(state, previous)
            if quiet:
                return WakeDecision(True, f"front {quiet} went quiet", "opportunity")

        if days_since_planner >= self.days_per_turn:
            return WakeDecision(True, "scheduled review", "scheduled")

        return WakeDecision(False, "nothing worth a decision")

    def _delegate(self, state: GameState) -> list[ActionCall]:
        """The AI-only arm of the hybrid experiment (#10), and nothing else.

        Off by default, because handing the army over is a strategic decision
        and a reflex has no business making it. The experiment needs a run
        where *no model* decides anything and the game's AI runs the war, so
        this stands in for that AI's default: every army delegated, and a
        defensive posture whenever a front is losing ground.
        """
        calls: list[ActionCall] = []
        if state.divisions and "all" not in state.delegated_armies:
            calls.append(ActionCall(
                name="delegate_army_to_ai", arguments={"army": "all", "delegate": True},
                rationale="reflex (AI-only arm): the game's AI runs the army",
            ))
        losing = any(f.pressure == "losing_ground" for f in state.fronts)
        if losing and state.posture != "defensive":
            calls.append(ActionCall(
                name="set_ai_posture", arguments={"posture": "defensive"},
                rationale="reflex (AI-only arm): a front is losing ground",
            ))
        return calls

    def _wartime_alarm(self, state: GameState, previous: GameState | None) -> str | None:
        """The things worth waking for in a war that no peacetime rule sees.

        Each fires on onset, not on a standing condition: the model hears about
        a pocket once, when it opens or grows, and the scheduled review covers
        the rest. That is the difference between a wake rate that stays flat in
        1941 and one that wakes every turn of a long siege.
        """
        before = {f.name: f for f in previous.fronts} if previous is not None else {}

        if self.wake_on_encirclement:
            for front in state.fronts:
                was = before.get(front.name)
                if front.pocket_divisions > (was.pocket_divisions if was else 0):
                    return (
                        f"encirclement forming on {front.name} "
                        f"({front.pocket_divisions} divisions at risk)"
                    )

        if self.wake_on_capital_threat:
            for front in state.fronts:
                was = before.get(front.name)
                if front.threatens_capital and not (was and was.threatens_capital):
                    return f"the capital is threatened from the {front.name} front"

        if self.wake_on_supply_collapse and previous is not None:
            # Only a front that was *known* to be fine can collapse: a front
            # whose supply was never observed has no "last week" to fall from.
            for front in state.fronts:
                was = before.get(front.name)
                if (
                    was is not None and was.supply is not None and front.supply is not None
                    and was.supply >= LOW_SUPPLY > front.supply
                ):
                    return (
                        f"supply collapsed on {front.name} "
                        f"({was.supply * 100:.0f}% -> {front.supply * 100:.0f}%)"
                    )

        if self.wake_on_ally_capitulation:
            for event in state.events:
                if event.kind == "ally_capitulated":
                    return f"an ally capitulated: {event.text}"
            if previous is not None:
                fell = _allies_lost(state, previous)
                if fell:
                    return f"an ally left the war: {', '.join(fell)}"
        return None

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

        if self.reflex_delegate:
            actions += self._delegate(state)

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


def _went_quiet(state: GameState, previous: GameState) -> str | None:
    """A front that was moving last turn and is not now -- often the most
    informative signal a war gives, and the moment a reserve can be moved."""
    before = {f.name: f for f in previous.fronts}
    for front in state.fronts:
        was = before.get(front.name)
        if was is not None and not is_quiet(was) and was.pressure != "stable" and is_quiet(front):
            return front.name
    return None


def _allies_lost(state: GameState, previous: GameState) -> list[str]:
    """Allies who were in a war last turn and are gone from it now, while the
    war itself continues -- a capitulation or a separate peace, and either way
    a front that is now yours alone."""
    now = {w.against: set(w.allies) for w in state.wars}
    lost: set[str] = set()
    for war in previous.wars:
        if war.against in now:
            lost |= set(war.allies) - now[war.against]
    return sorted(lost)
