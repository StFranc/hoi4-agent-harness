import pytest

from hoi4_harness.actions import registry
from hoi4_harness.agent.llm import build_llm
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.config import HarnessConfig, LLMConfig
from hoi4_harness.eval import SCENARIOS, run_scenario
from hoi4_harness.types import ActionCall


def test_provider_factory_defaults_to_a_model_per_provider():
    assert LLMConfig(provider="anthropic").model == "claude-opus-5"
    assert LLMConfig(provider="openai").model == "gpt-5"
    assert LLMConfig(provider="google").model == "gemini-2.5-pro"


def test_scripted_provider_needs_no_sdk_and_no_key():
    client = build_llm(LLMConfig(provider="scripted"))
    assert isinstance(client, ScriptedClient)


def test_unknown_provider_names_the_known_ones():
    with pytest.raises(ValueError, match="anthropic"):
        build_llm(LLMConfig(provider="skynet"))


def test_calls_produced_by_the_offline_policy_validate_against_the_catalog():
    from hoi4_harness.adapters.mock import MockAdapter
    from hoi4_harness.agent.llm.base import Msg
    from hoi4_harness.observation import render_full

    adapter = MockAdapter()
    brief = render_full(adapter.read_state())
    tools = registry.tool_specs(set(adapter.supported_actions))
    response = ScriptedClient().complete("system", [Msg("user", brief)], tools)

    assert response.tool_calls
    for call in response.tool_calls:
        action = ActionCall(call.name, call.arguments, call.id)
        assert registry.check(action, set(adapter.supported_actions)) is None, call


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_scenarios_run_and_score(key):
    config = HarnessConfig(adapter="mock", planner=LLMConfig(provider="scripted"))
    card = run_scenario(key, config)
    assert 0.0 <= card.score <= 1.0
    # Turns are a cost now, not the bound: the run stops at the scenario's end
    # date, and the cap only says how long it may take to get there.
    assert 0 < card.turns <= SCENARIOS[key].max_turns
    assert card.reached_end_date is True
    assert card.end_date >= SCENARIOS[key].until
    assert set(card.objectives) == {o.name for o in SCENARIOS[key].objectives}
