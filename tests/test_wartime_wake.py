"""Wartime wake rules (#13): each fires on the onset of its condition, not while it stands."""

import copy

import pytest

from hoi4_harness.agent.policy import Policy
from hoi4_harness.config import HarnessConfig
from hoi4_harness.types import Front, GameEvent, GameState, War


def _at_war(**front_fields) -> GameState:
    front = Front(name="karelia", enemy="SOV", divisions_friendly=20, divisions_enemy=30,
                  **front_fields)
    return GameState(
        country="FIN", national_focus="winter_war", focus_days_remaining=20,
        wars=[War(against="SOV", since="1939-11-30", allies=["SWE"])],
        fronts=[front],
    )


def _wake(state, previous=None, **flags):
    return Policy(**flags).should_wake(state, days_since_planner=0, previous=previous)


def test_a_quiet_war_does_not_wake_the_planner_between_reviews():
    state = _at_war(supply=0.9)
    assert not _wake(state, previous=copy.deepcopy(state)).wake


def test_an_encirclement_forming_wakes_critically():
    before = _at_war()
    after = _at_war(pocket_divisions=6)
    decision = _wake(after, before)
    assert decision.wake and decision.urgency == "critical"
    assert "encirclement forming on karelia (6 divisions at risk)" in decision.reason


def test_a_standing_pocket_does_not_wake_every_turn_but_a_growing_one_does():
    assert not _wake(_at_war(pocket_divisions=6), _at_war(pocket_divisions=6)).wake
    assert _wake(_at_war(pocket_divisions=9), _at_war(pocket_divisions=6)).wake


def test_supply_collapse_on_a_front_that_was_fine_last_week():
    decision = _wake(_at_war(supply=0.3), _at_war(supply=0.8))
    assert decision.wake and decision.urgency == "critical"
    assert "supply collapsed on karelia (80% -> 30%)" in decision.reason


def test_supply_that_was_never_observed_cannot_collapse():
    assert not _wake(_at_war(supply=0.3), _at_war(supply=None)).wake
    assert not _wake(_at_war(supply=0.3), None).wake


def test_the_capital_coming_under_threat_wakes_once():
    decision = _wake(_at_war(threatens_capital=True), _at_war())
    assert decision.wake and "capital is threatened" in decision.reason
    assert not _wake(_at_war(threatens_capital=True), _at_war(threatens_capital=True)).wake


def test_an_ally_capitulating_wakes_from_the_event():
    state = _at_war()
    state.events = [GameEvent(kind="ally_capitulated", text="Sweden has capitulated.")]
    decision = _wake(state, copy.deepcopy(_at_war()))
    assert decision.wake and "an ally capitulated" in decision.reason


def test_an_ally_vanishing_from_a_continuing_war_wakes():
    after = _at_war()
    after.wars[0].allies = []
    decision = _wake(after, _at_war())
    assert decision.wake and "an ally left the war: SWE" in decision.reason


def test_a_front_that_just_went_quiet_is_an_opportunity():
    decision = _wake(_at_war(), _at_war(pressure="losing_ground"))
    # losing ground -> stable: the previous state would have woken on its own;
    # this turn is the one where a reserve can be moved.
    assert decision.wake and decision.urgency == "opportunity"
    assert decision.reason == "front karelia went quiet"


def test_wartime_rules_are_gated_on_being_at_war():
    peace = _at_war(pocket_divisions=6)
    peace.wars = []
    assert not _wake(peace, _at_war()).wake


@pytest.mark.parametrize(
    ("flag", "after", "before"),
    [
        ("wake_on_encirclement", _at_war(pocket_divisions=6), _at_war()),
        ("wake_on_supply_collapse", _at_war(supply=0.3), _at_war(supply=0.8)),
        ("wake_on_capital_threat", _at_war(threatens_capital=True), _at_war()),
        ("wake_on_front_quiet", _at_war(), _at_war(pressure="losing_ground")),
    ],
)
def test_each_wartime_rule_can_be_switched_off(flag, after, before):
    assert _wake(after, before).wake
    assert not _wake(after, before, **{flag: False}).wake


def test_the_flags_are_settable_from_a_profile():
    config = HarnessConfig().merge({"wake_on_front_quiet": False, "wake_on_encirclement": False})
    assert config.wake_on_front_quiet is False and config.wake_on_encirclement is False


def test_the_loop_hands_the_policy_last_turns_state():
    from hoi4_harness.adapters.mock import MockAdapter
    from hoi4_harness.agent.loop import AgentLoop
    from hoi4_harness.env import HOI4Env

    seen = []

    class Spy(Policy):
        def should_wake(self, state, days_since_planner, previous=None):
            seen.append(previous)
            return super().should_wake(state, days_since_planner, previous)

    config = HarnessConfig(planner_enabled=False)
    loop = AgentLoop(HOI4Env(MockAdapter(), config), planner=None, config=config)
    loop.policy = Spy(planner_enabled=False)
    loop.run(3)
    assert seen[0] is None
    assert seen[1] is not None and seen[1].date == "1936-01-01"
    assert seen[2].date == "1936-01-08"


# --- losing ground (#70): onset, not the standing condition -----------------

def test_a_front_starting_to_lose_ground_wakes_critically():
    decision = _wake(_at_war(pressure="losing_ground"), _at_war())
    assert decision.wake and decision.urgency == "critical"
    assert decision.reason == "losing ground on karelia"


def test_a_front_that_stays_losing_does_not_wake_every_turn():
    assert not _wake(_at_war(pressure="losing_ground"), _at_war(pressure="losing_ground")).wake


def test_a_front_that_is_losing_at_the_first_look_wakes():
    # No last turn to compare with: the model has not heard about it yet.
    assert _wake(_at_war(pressure="losing_ground"), None).wake


def test_a_front_that_recovers_and_turns_again_wakes_again():
    assert _wake(_at_war(pressure="losing_ground"), _at_war(pressure="advancing")).wake


def test_losing_ground_can_be_switched_off():
    after, before = _at_war(pressure="losing_ground"), _at_war()
    assert not _wake(after, before, wake_on_losing_ground=False).wake
    config = HarnessConfig().merge({"wake_on_losing_ground": False})
    assert config.wake_on_losing_ground is False


def test_a_front_losing_for_several_turns_wakes_the_planner_once():
    """Through the real loop: defensive_war starts on a losing front and the
    front stays losing. With only the schedule and the alarms on, the losing
    front is worth one wake, not one per turn."""
    from hoi4_harness.adapters.mock import MockAdapter
    from hoi4_harness.agent.llm import build_llm
    from hoi4_harness.agent.loop import AgentLoop
    from hoi4_harness.config import LLMConfig
    from hoi4_harness.env import HOI4Env
    from hoi4_harness.eval.scenarios import SCENARIOS

    scenario = SCENARIOS["defensive_war"]
    config = HarnessConfig(adapter="mock", planner=LLMConfig(provider="scripted"),
                           wake_on_free_research_slot=False, wake_on_no_focus=False)
    adapter = MockAdapter(seed=scenario.seed, country=scenario.country,
                          start=scenario.start, start_state=scenario.start_state)
    env = HOI4Env(adapter, config)
    env.reset()
    loop = AgentLoop(env, planner=build_llm(config.planner), config=config)

    seen = []

    class Spy(Policy):
        def should_wake(self, state, days_since_planner, previous=None):
            decision = super().should_wake(state, days_since_planner, previous)
            seen.append((state, decision))
            return decision

    loop.policy = Spy(days_per_turn=config.days_per_turn, wake_on_free_research_slot=False,
                      wake_on_no_focus=False)
    loop.run(8)

    losing_turns = [s for s, _ in seen
                    if any(f.pressure == "losing_ground" for f in s.fronts)]
    assert len(losing_turns) >= 5          # the front stays losing throughout
    losing_wakes = [d for _, d in seen if d.wake and "losing ground" in d.reason]
    assert len(losing_wakes) == 1, [d.reason for _, d in seen]
    assert "losing ground" in seen[0][1].reason
    assert not all(d.wake for _, d in seen)


def test_the_loop_passes_the_losing_ground_flag_to_the_policy():
    from hoi4_harness.adapters.mock import MockAdapter
    from hoi4_harness.agent.loop import AgentLoop
    from hoi4_harness.env import HOI4Env

    config = HarnessConfig(planner_enabled=False, wake_on_losing_ground=False)
    loop = AgentLoop(HOI4Env(MockAdapter(), config), planner=None, config=config)
    assert loop.policy.wake_on_losing_ground is False


def test_measure_groups_losing_ground_wakes_apart_from_a_front_going_quiet():
    from hoi4_harness.eval.measure import _reason_key

    assert _reason_key("losing ground on center") == "losing ground on"
    assert _reason_key("losing ground on karelia") == "losing ground on"
    assert _reason_key("front center went quiet") == "front"
