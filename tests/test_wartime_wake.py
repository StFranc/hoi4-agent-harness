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
