"""The scenario suite: what makes a score on these scenarios mean something.

Three properties are pinned here. A scenario's start actually reaches the mock
(the scenario owns its opening position; the adapter has no scenario names in
it); every objective reads only fields the mock actually populates, so no score
ever turns on an observation the agent could not have seen; and each scenario
sits between two failures -- the reflex-only baseline cannot pass it (failable,
asserted against the recorded file) and a small competent policy can (passable,
asserted by playing one).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.loop import RunReport
from hoi4_harness.config import HarnessConfig, LLMConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval import SCENARIOS, score
from hoi4_harness.eval.baselines import load
from hoi4_harness.eval.runner import run_scenario
from hoi4_harness.eval.scenarios import most_turns
from hoi4_harness.observation import snapshot
from hoi4_harness.types import ActionCall, GameState


def play(scenario, decide) -> tuple[GameState, list[GameState]]:
    """Run a scenario on its own mock, asking one tiny policy once per turn.

    Stops where the scenario stops -- at its end date, with max_turns only as a
    backstop -- and returns the per-turn history the sustained objectives read.
    """
    adapter = MockAdapter(seed=scenario.seed, country=scenario.country,
                          start=scenario.start, start_state=scenario.start_state)
    config = HarnessConfig(adapter="mock", planner_enabled=False,
                           operational_control=scenario.operational_control)
    env = HOI4Env(adapter, config)
    env.reset()
    history = []
    for _ in range(scenario.max_turns):
        state = env.read_state()
        history.append(snapshot(state))
        env.act_many(decide(state))
        state = env.advance()
        if state.date >= scenario.until:
            break
    return env.read_state(), history


def run_card(scenario, decide):
    final, history = play(scenario, decide)
    return score(scenario, final, RunReport(), history)


# --- the starts --------------------------------------------------------------

def test_the_default_mock_start_is_what_the_baselines_were_measured_against():
    state = MockAdapter().read_state()
    assert state.country == "SWE" and state.date == "1936-01-01"
    assert state.civilian_factories == 12 and state.military_factories == 5
    assert state.stockpiles["infantry_equipment_1"] == 4200
    assert state.fronts == [] and state.wars == []
    assert state.resources["rubber"] > 0 and state.resources["oil"] > 0


def test_defensive_war_starts_at_war_outnumbered_on_a_losing_front():
    scenario = SCENARIOS["defensive_war"]
    state = MockAdapter(seed=scenario.seed, country=scenario.country,
                        start=scenario.start, start_state=scenario.start_state).read_state()

    assert [war.against for war in state.wars] == ["SOV"]
    front = state.fronts[0]
    assert (front.name, front.divisions_friendly, front.divisions_enemy) == ("karelia", 12, 50)
    assert front.pressure == "losing_ground"
    assert {g.location: g.count for g in state.divisions} == {"karelia": 12, "home": 12}


def test_resource_starved_starts_without_the_resources_it_cannot_mine():
    scenario = SCENARIOS["resource_starved"]
    state = MockAdapter(seed=scenario.seed, country=scenario.country,
                        start=scenario.start, start_state=scenario.start_state).read_state()

    assert state.resources["oil"] == 0 and state.resources["rubber"] == 0
    assert state.resources["steel"] == 40            # merged over, not replaced
    assert state.civilian_factories == 12            # otherwise the classic start


def test_rearmament_race_starts_inadequate_and_owns_its_war_date():
    scenario = SCENARIOS["rearmament_race"]
    adapter = MockAdapter(seed=scenario.seed, country=scenario.country,
                          start=scenario.start, start_state=scenario.start_state)
    state = adapter.read_state()

    assert state.civilian_factories == 6 and state.military_factories == 2
    assert sum(g.count for g in state.divisions) == 4
    assert state.stockpiles["infantry_equipment_1"] == 1_500

    late = adapter.advance(900)                      # runs to the first critical event
    assert late.date == "1938-06-01"
    assert any(e.kind == "war" and e.severity == "critical" for e in late.events)


def test_recovery_starts_with_the_mills_pointed_at_the_wrong_lines():
    scenario = SCENARIOS["recovery"]
    state = MockAdapter(seed=scenario.seed, country=scenario.country,
                        start=scenario.start, start_state=scenario.start_state).read_state()

    assert state.military_factories == 8
    assigned = {line.equipment: line.factories for line in state.production}
    assert assigned == {"support_equipment": 4, "artillery": 2}
    assert state.stockpiles["infantry_equipment_1"] == 800
    assert state.construction == []
    assert all(slot.technology is None for slot in state.research)


# --- what the objectives may read -------------------------------------------

class _AccessRecorder:
    """Stands in for a GameState and writes down every field a check reads."""

    def __init__(self, inner):
        self._inner = inner
        self.read: set[str] = set()

    def __getattr__(self, name):
        self.read.add(name)
        return getattr(self._inner, name)


# Fields the mock assigns even though the value equals the GameState default --
# no focus running, clock paused. Real observations, deliberately None or zero.
_ASSIGNED_AS_DEFAULT = {"date", "speed", "national_focus", "focus_days_remaining", "events"}


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_objectives_only_read_fields_the_mock_populates(key):
    scenario = SCENARIOS[key]
    state = MockAdapter(seed=scenario.seed, country=scenario.country,
                        start=scenario.start, start_state=scenario.start_state).read_state()
    bare = GameState().to_dict()
    populated = {name for name, value in state.to_dict().items()
                 if value != bare[name]} | _ASSIGNED_AS_DEFAULT

    seen: set[str] = set()
    for objective in scenario.objectives:
        recorder = _AccessRecorder(state)
        # A sustained objective reads the same fields, just across a history.
        assert objective.holds(recorder, [recorder]) in (True, False)
        seen |= recorder.read

    assert seen <= populated, f"{key}: objectives read {seen - populated}"


# --- passable and failable ---------------------------------------------------

@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_every_scenario_is_failable(key):
    assert load()[key]["score"] < 1.0


def _hold_karelia(state):
    """Triage: reinforce the collapsing front, then delegate it defensively."""
    if state.delegated_armies:
        return []
    return [
        ActionCall("deploy_divisions",
                   {"template": "Infantry", "count": 3, "location": "karelia"}),
        ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}),
        ActionCall("set_ai_posture", {"posture": "defensive"}),
    ]


def test_defensive_war_rewards_triage_and_punishes_ignoring_it():
    scenario = SCENARIOS["defensive_war"]
    assert run_card(scenario, _hold_karelia).score == 1.0
    # Delegating without reinforcing is not triage: 24 divisions cannot hold
    # 50, and the mock bleeds them. Half the answer scores nothing.
    assert run_card(scenario, lambda state: []).score == 0.0


def _fix_the_shortage(state):
    if state.resources.get("rubber", 0) > 0:
        return []
    return [ActionCall("set_trade",
                       {"resource": "rubber", "from_country": "GER", "factories": 1})]


def test_resource_starved_is_passed_by_noticing_and_fixing_the_shortage():
    assert run_card(SCENARIOS["resource_starved"], _fix_the_shortage).score == 1.0


def _rearm(state):
    """Sequence: military factories first, production staffed, then deploy."""
    calls = []
    queued = sum(1 for item in state.construction if item.building == "military_factory")
    if state.military_factories + queued < 6:
        calls.append(ActionCall("queue_construction",
                                {"building": "military_factory", "state": "capital"}))
    line = next((c for c in state.production if c.equipment == "infantry_equipment_1"), None)
    if (line.factories if line else 0) < state.military_factories:
        calls.append(ActionCall("set_production",
                                {"equipment": "infantry_equipment_1",
                                 "factories": state.military_factories}))
    total = sum(g.count for g in state.divisions)
    stock = state.stockpiles.get("infantry_equipment_1", 0)
    if total < 10 and stock >= 900:
        calls.append(ActionCall("deploy_divisions",
                                {"template": "Infantry",
                                 "count": min(10 - total, stock // 900)}))
    return calls


def test_rearmament_race_rewards_sequencing_under_the_deadline():
    assert run_card(SCENARIOS["rearmament_race"], _rearm).score == 1.0


def _diagnose(state):
    """Recovery: rebuild the infantry line, start research, build something."""
    calls = []
    line = next((c for c in state.production if c.equipment == "infantry_equipment_1"), None)
    if (line.factories if line else 0) < 4:
        support = next((c for c in state.production if c.equipment == "support_equipment"), None)
        if support and support.factories > 2:
            calls.append(ActionCall("set_production",
                                    {"equipment": "support_equipment", "factories": 2}))
        calls.append(ActionCall("set_production",
                                {"equipment": "infantry_equipment_1", "factories": 4}))
    if any(slot.technology is None for slot in state.research):
        calls.append(ActionCall("start_research", {"technology": "construction1"}))
    if not state.construction:
        calls.append(ActionCall("queue_construction",
                                {"building": "civilian_factory", "state": "capital", "count": 2}))
    return calls


def test_recovery_rewards_diagnosis_over_the_obvious_defaults():
    assert run_card(SCENARIOS["recovery"], _diagnose).score == 1.0


# --- bounding (issue #50) ----------------------------------------------------

def test_a_scenario_stops_at_its_date_however_fast_the_turns_go():
    """The bug this replaces: two policies with different pacing were scored six
    years apart, against objectives that only grow with time."""
    scenario = SCENARIOS["economy_ramp"]
    config = HarnessConfig(adapter="mock", planner=LLMConfig(provider="scripted"))

    slow = run_scenario(scenario, config)
    fast = run_scenario(replace(scenario, seed=99), config)

    for card in (slow, fast):
        assert card.reached_end_date is True
        assert card.end_date >= scenario.until


def test_a_run_that_never_reaches_the_date_is_reported_not_scored_as_if_it_had():
    scenario = replace(SCENARIOS["war_readiness"], max_turns=5)
    card = run_scenario(scenario, HarnessConfig(adapter="mock",
                                                planner=LLMConfig(provider="scripted")))
    assert card.reached_end_date is False
    assert "STOPPED SHORT" in card.render()


def test_the_cost_line_reads_as_cost_not_configuration():
    card = run_scenario("economy_ramp", HarnessConfig(adapter="mock",
                                                      planner=LLMConfig(provider="scripted")))
    line = next(x for x in card.render().splitlines() if "reached" in x)
    assert "model calls" in line and "turns" in line


def test_a_sustained_objective_is_not_satisfied_by_the_last_turn_alone():
    """most_turns() is the whole point of #50's second half: a focus running at
    the instant the run stopped says nothing about how it was played."""
    idle = GameState()
    busy = GameState(national_focus="industrial_effort")
    sustained = most_turns(lambda s: s.national_focus is not None)

    assert not sustained([idle] * 9 + [busy])       # right at the end only
    assert sustained([busy] * 9 + [idle])           # held throughout
    assert not sustained([])
