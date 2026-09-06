from hoi4_harness.actions import registry
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.hybrid import DIRECTIVE_ACTIONS, OPERATIONAL_ACTIONS, actions_for_mode
from hoi4_harness.agent.prompts import build_system
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.observation import render_full
from hoi4_harness.types import ActionCall

ALL = OPERATIONAL_ACTIONS | DIRECTIVE_ACTIONS | {"note"}


def hybrid_env(**overrides):
    config = HarnessConfig(
        operational_control="ai", dry_run=False, require_confirmation=False, **overrides
    )
    return HOI4Env(MockAdapter(), config)


def test_the_two_vocabularies_are_never_offered_together():
    ai_mode = actions_for_mode(ALL, "ai")
    llm_mode = actions_for_mode(ALL, "llm")
    assert ai_mode & OPERATIONAL_ACTIONS == set()
    assert llm_mode & DIRECTIVE_ACTIONS == set()
    assert "note" in ai_mode and "note" in llm_mode


def test_hybrid_mode_hides_direct_army_orders_even_from_a_whitelist():
    env = hybrid_env(enabled_actions=["set_army_order", "note", "set_ai_directive"])
    assert "set_army_order" not in env.allowed_actions


def test_the_prompt_tells_the_model_which_layer_it_is():
    hybrid = build_system(guidance="hybrid", operational_control="ai")
    direct = build_system(guidance="doctrine", operational_control="llm")
    assert "you do not move units" in hybrid
    assert "You command directly" in direct


def test_delegating_and_directing_changes_state_the_agent_can_see():
    env = hybrid_env()
    assert env.act(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True})).ok
    assert env.act(ActionCall("set_ai_posture", {"posture": "defensive"})).ok
    assert env.act(
        ActionCall("set_ai_directive", {"directive": "protect", "target": "FIN", "weight": 150})
    ).ok

    brief = render_full(env.read_state())
    assert "AI control:" in brief
    assert "delegated to the game AI" in brief
    assert "protect FIN (150)" in brief


def test_posture_without_delegation_is_refused_rather_than_silently_ignored():
    env = hybrid_env()
    result = env.act(ActionCall("set_ai_posture", {"posture": "offensive"}))
    assert not result.ok and "No armies are delegated" in result.message


def test_a_directive_replaces_rather_than_duplicates_itself():
    env = hybrid_env()
    for weight in (100, 250):
        env.act(ActionCall("set_ai_directive", {"directive": "invade", "target": "GER",
                                                "weight": weight}))
    directives = env.read_state().ai_directives
    assert directives == ["invade GER (250)"]


def test_clearing_directives_resets_posture_too():
    env = hybrid_env()
    env.act(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    env.act(ActionCall("set_ai_posture", {"posture": "offensive"}))
    env.act(ActionCall("set_ai_directive", {"directive": "contain", "target": "SOV"}))
    assert env.act(ActionCall("clear_ai_directives", {})).ok
    state = env.read_state()
    assert state.ai_directives == [] and state.posture is None


def test_directives_are_confirmation_gated_in_dry_run():
    env = HOI4Env(MockAdapter(), HarnessConfig(operational_control="ai", dry_run=True))
    result = env.act(ActionCall("set_ai_directive", {"directive": "invade", "target": "GER"}))
    assert not result.ok and result.error_kind == "rejected"


def test_directive_arguments_are_validated_like_any_other_action():
    problem = registry.check(ActionCall("set_ai_directive", {"directive": "nuke", "target": "GER"}))
    assert problem and "invade" in problem.message
