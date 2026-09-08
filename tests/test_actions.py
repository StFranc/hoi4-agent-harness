import pytest

from hoi4_harness.actions import catalog, registry
from hoi4_harness.actions.validate import validate
from hoi4_harness.types import ActionCall


def test_every_spec_has_a_well_formed_schema():
    for spec in catalog.ACTIONS:
        assert spec.parameters["type"] == "object"
        assert spec.parameters.get("additionalProperties") is False
        for name in spec.parameters.get("required", []):
            assert name in spec.parameters["properties"], f"{spec.name}: {name} not declared"


def test_start_research_declares_no_slot_argument():
    # An argument nothing reads is a silent no-op: no adapter implements `slot`
    # (the mock fills the first free slot and reports where research landed), so
    # declaring it let a model pass slots 0-4 against a three-slot state and be
    # told it succeeded -- issue #39.
    spec = catalog.get("start_research")
    assert spec is not None and "slot" not in spec.parameters["properties"]


def test_unknown_action_is_rejected_with_a_hint():
    problem = registry.check(ActionCall("set_national_focos", {"focus_id": "x"}))
    assert problem is not None and problem.error_kind == "invalid_action"
    assert "set_national_focus" in problem.message


def test_missing_and_unknown_arguments_are_named():
    problem = registry.check(ActionCall("queue_construction", {"building": "civilian_factory"}))
    assert problem and "state" in problem.message

    problem = registry.check(
        ActionCall("queue_construction",
                   {"building": "civilian_factory", "state": "a", "colour": "red"})
    )
    assert problem and "colour" in problem.message


def test_enum_and_range_violations_say_what_was_allowed():
    problem = registry.check(ActionCall("queue_construction",
                                        {"building": "space_elevator", "state": "a"}))
    assert problem and "civilian_factory" in problem.message

    problem = registry.check(ActionCall("set_game_speed", {"speed": 9}))
    assert problem and "maximum" in problem.message


def test_booleans_are_not_integers():
    assert validate(True, {"type": "integer"})


@pytest.mark.parametrize("name", ["diplomacy", "set_army_order"])
def test_irreversible_actions_are_flagged(name):
    assert registry.needs_confirmation(ActionCall(name, {}))


def test_tool_specs_are_filtered_to_what_the_adapter_supports():
    specs = registry.tool_specs({"note", "advance_time"})
    assert {s.name for s in specs} == {"note", "advance_time"}
