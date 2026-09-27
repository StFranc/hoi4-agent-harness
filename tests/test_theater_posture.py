"""Theater-scoped posture (#9): two theaters can hold different postures at once."""

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.observation import render_delta, render_full
from hoi4_harness.observation.builder import snapshot
from hoi4_harness.types import ActionCall, DivisionGroup, Front, ScenarioStart, War


def _two_front_war() -> MockAdapter:
    return MockAdapter(
        country="GER",
        start="1939-09-01",
        start_state=ScenarioStart(
            wars=[War(against="POL"), War(against="FRA")],
            fronts=[
                Front(name="east", enemy="POL", divisions_enemy=10),
                Front(name="west", enemy="FRA", divisions_enemy=30),
            ],
            divisions=[
                DivisionGroup(template="Infantry", count=20, location="east"),
                DivisionGroup(template="Infantry", count=16, location="west"),
                DivisionGroup(template="Infantry", count=6, location="home"),
            ],
        ),
    )


def _posture(adapter, posture, theater=None):
    arguments = {"posture": posture} | ({"theater": theater} if theater else {})
    return adapter.apply(ActionCall("set_ai_posture", arguments))


def test_two_theaters_hold_different_postures_and_the_brief_shows_both():
    adapter = _two_front_war()
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    assert _posture(adapter, "offensive", "east").ok
    assert _posture(adapter, "defensive", "west").ok
    adapter.advance(1)
    state = adapter.read_state()

    assert state.theater_postures == {"east": "offensive", "west": "defensive"}
    brief = render_full(state)
    assert "theaters: east offensive, west defensive" in brief

    east = next(f for f in state.fronts if f.name == "east")
    west = next(f for f in state.fronts if f.name == "west")
    assert east.stance == "offensive" and east.pressure == "advancing"
    assert west.stance == "hold"
    # The defended theater is where the AI sends the reserve.
    assert west.divisions_friendly == 22


def test_a_theater_posture_overrides_the_global_one_only_there():
    adapter = _two_front_war()
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    _posture(adapter, "defensive")
    _posture(adapter, "offensive", "east")
    assert adapter.posture_on("east") == "offensive"
    assert adapter.posture_on("west") == "defensive"


def test_an_unknown_theater_is_rejected_with_the_real_ones():
    adapter = _two_front_war()
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    result = _posture(adapter, "offensive", "africa")
    assert not result.ok and result.error_kind == "invalid_args"
    assert "Fronts: east, west" in result.message


def test_the_delta_reports_a_theater_changing_posture():
    adapter = _two_front_war()
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    before = snapshot(adapter.read_state())
    _posture(adapter, "offensive", "east")
    text = render_delta(before, adapter.read_state())
    assert "AI posture on east: global -> offensive" in text


def test_clearing_directives_clears_theater_postures_too():
    adapter = _two_front_war()
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    _posture(adapter, "offensive", "east")
    adapter.apply(ActionCall("clear_ai_directives", {}))
    assert adapter.read_state().theater_postures == {}
