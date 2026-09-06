import json

import pytest

from hoi4_harness import guidance
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.policy import Policy
from hoi4_harness.agent.prompts import MECHANICS, build_system
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, GameState


def test_every_builtin_guidance_pack_loads():
    for name in guidance.available():
        assert guidance.load(name).strip()


def test_no_guidance_leaves_only_mechanics_and_the_control_split():
    system = build_system(guidance=None)
    assert system.startswith(MECHANICS)
    assert "You command directly" in system      # the control split is never implicit
    assert len(system) < len(build_system(guidance="doctrine"))


def test_unconstrained_guidance_says_nothing_is_off_limits():
    system = build_system(guidance="unconstrained")
    assert "Nothing is off limits inside the game" in system
    assert MECHANICS in system          # mechanics are never dropped


def test_guidance_can_come_from_a_file(tmp_path):
    custom = tmp_path / "doctrine.md"
    custom.write_text("Rush tanks. Ignore everything else.", encoding="utf-8")
    assert "Rush tanks" in build_system(guidance=custom)


def test_unknown_guidance_pack_lists_the_real_ones():
    with pytest.raises(FileNotFoundError, match="doctrine"):
        guidance.load("wehraboo")


def test_system_prompt_can_be_replaced_entirely(tmp_path):
    override = tmp_path / "system.txt"
    override.write_text("You are a potato.", encoding="utf-8")
    system = build_system(guidance="doctrine", system_prompt_path=override)
    assert system == "You are a potato."
    assert MECHANICS not in system


def test_action_whitelist_narrows_what_the_model_is_offered():
    config = HarnessConfig(enabled_actions=["note", "advance_time", "set_production"])
    env = HOI4Env(MockAdapter(), config)
    assert env.allowed_actions == {"note", "advance_time", "set_production"}


def test_blacklist_subtracts_from_adapter_capability():
    config = HarnessConfig(disabled_actions=["set_production"])
    env = HOI4Env(MockAdapter(), config)
    assert "set_production" not in env.allowed_actions
    assert "note" in env.allowed_actions


class _WarCapableMock(MockAdapter):
    """A mock that claims it can give army orders, so the gate is reachable."""

    supported_actions = MockAdapter.supported_actions | {"set_army_order"}

    def _do_set_army_order(self, call):
        return self._ok(call, "Order issued.")


def test_confirmation_gate_blocks_irreversible_actions_in_dry_run():
    gated = ActionCall("set_army_order", {"army": "1st", "order": "offensive"})
    env = HOI4Env(_WarCapableMock(), HarnessConfig(dry_run=True))
    result = env.act(gated)
    assert not result.ok and result.error_kind == "rejected"
    assert "dry-run" in result.message


def test_confirmation_gate_can_be_switched_off():
    gated = ActionCall("set_army_order", {"army": "1st", "order": "offensive"})
    env = HOI4Env(_WarCapableMock(), HarnessConfig(dry_run=False, require_confirmation=False))
    assert env.act(gated).ok


def test_a_human_hook_can_veto_a_gated_action():
    gated = ActionCall("set_army_order", {"army": "1st", "order": "offensive"})
    env = HOI4Env(_WarCapableMock(), HarnessConfig(dry_run=False, require_confirmation=True))
    env.confirm_hook = lambda call: False
    assert env.act(gated).error_kind == "rejected"
    env.confirm_hook = lambda call: True
    assert env.act(gated).ok


def test_reflexes_can_be_disabled_so_the_model_decides_everything():
    state = GameState(civilian_factories=10, military_factories=5)
    assert Policy(reflex_enabled=True).reflex_actions(state)
    assert Policy(reflex_enabled=False).reflex_actions(state) == []


def test_wake_rules_are_configurable():
    idle = GameState(national_focus=None)
    assert Policy(wake_on_no_focus=True).should_wake(idle, 0).wake
    assert not Policy(wake_on_no_focus=False, wake_on_free_research_slot=False).should_wake(
        idle, 0
    ).wake


def test_a_profile_file_configures_the_whole_run(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "guidance": "coach",
                "days_per_turn": 21,
                "country": "GER",
                "planner": {"provider": "scripted", "model": "stub"},
                "budget": {"max_usd": 1.5},
            }
        ),
        encoding="utf-8",
    )
    config = HarnessConfig.from_file(path)
    assert config.guidance == "coach"
    assert config.days_per_turn == 21
    assert config.country == "GER"
    assert config.planner.model == "stub"
    assert config.budget.max_usd == 1.5


def test_a_profile_with_an_unknown_key_fails_loudly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"guidence": "coach"}), encoding="utf-8")
    with pytest.raises(KeyError, match="guidence"):
        HarnessConfig.from_file(path)
