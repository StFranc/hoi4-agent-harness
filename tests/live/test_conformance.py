"""What actually breaks against a live provider, one cheap call per check.

Wire formats drift and a scripted suite cannot see it. These check the four
things that have to hold for the loop to work at all: the tool block is
accepted, a tool call comes back as a valid ActionCall, a tool result can be
sent back for a second round, and usage is reported. Prefix caching is checked
where the provider promises it.
"""

from __future__ import annotations

import pytest

from hoi4_harness.actions import registry
from hoi4_harness.agent.llm.base import Msg, ToolResult
from hoi4_harness.agent.prompts import build_system
from hoi4_harness.types import ActionCall

ASK = (
    "Turn 1. You were woken because: 3 research slot(s) idle.\n\n"
    "## Sweden (SWE) -- 1936-01-01\nResearch: idle | 3 SLOT(S) FREE\n\n"
    "Call start_research exactly once, for technology 'construction1'. Nothing else."
)
TOOLS = sorted({"start_research", "note", "advance_time"})


def _first_call(provider_client):
    _, client = provider_client
    system = build_system("minimal")
    tools = registry.tool_specs(set(TOOLS))
    response = client.complete(system, [Msg(role="user", text=ASK)], tools)
    return client, system, tools, response


def test_the_tool_block_is_accepted_and_a_call_parses_into_a_valid_action(live):
    _, _, _, response = _first_call(live)
    assert response.tool_calls, f"no tool call came back: stop={response.stop_reason} text={response.text!r}"
    call = response.tool_calls[0]
    action = ActionCall(name=call.name, arguments=call.arguments, call_id=call.id)
    assert registry.check(action, set(TOOLS)) is None, f"invalid call: {call}"
    assert call.id, "a tool call needs an id to send its result back"


def test_usage_is_populated(live):
    _, _, _, response = _first_call(live)
    usage = response.usage
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    assert 0 <= usage.cached_input_tokens <= usage.input_tokens
    assert response.model


def test_a_tool_result_round_trips_into_a_second_round(live):
    client, system, tools, response = _first_call(live)
    assert response.tool_calls
    call = response.tool_calls[0]
    messages = [
        Msg(role="user", text=ASK),
        Msg(role="assistant", text=response.text, tool_calls=response.tool_calls),
        Msg(role="user", tool_results=[
            ToolResult(call_id=c.id, name=c.name, content='{"ok": true, "message": "Researching."}')
            for c in response.tool_calls
        ]),
    ]
    second = client.complete(system, messages, tools)
    # Accepted is the claim; what the model says next is its own business.
    assert second.stop_reason != "error"
    assert second.usage.input_tokens > response.usage.input_tokens, (
        f"the second round should carry the first ({call.name}) in its input"
    )


def test_prefix_caching_is_reported_where_the_provider_promises_it(live):
    provider, client = live
    if provider != "anthropic":
        # OpenAI and Gemini cache implicitly and without a guarantee on the
        # second call; a zero there is not a conformance failure.
        pytest.skip(f"{provider} caching is implicit and best-effort")
    system = build_system("doctrine")
    tools = registry.tool_specs(None)
    ask = [Msg(role="user", text=ASK)]
    client.complete(system, ask, tools)
    second = client.complete(system, ask, tools)
    assert second.usage.cached_input_tokens > 0, (
        "the system+tools prefix was marked cacheable and the second identical call read "
        "nothing from cache: either the prefix is below the model's minimum cacheable "
        "length or the cache breakpoints stopped reaching the wire"
    )
