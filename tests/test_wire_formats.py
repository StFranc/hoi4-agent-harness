"""Provider wire conversion.

Three vendors, three shapes, and the harness translates its own neutral message
type into each. None of it was covered, which meant the provider layer's
correctness rested on nothing -- and the Gemini path is flagged in the README as
written against an SDK surface that moves.

These exercise the conversion functions directly: no SDK import, no network, no
key. What they pin down is the part that is easy to get subtly wrong and
impossible to notice offline -- where tool results attach, and in what order.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness.actions.registry import ToolSpec, tool_specs
from hoi4_harness.agent.llm import anthropic_provider as anthropic
from hoi4_harness.agent.llm import google_provider as google
from hoi4_harness.agent.llm import openai_provider as openai
from hoi4_harness.agent.llm.base import Msg, ToolCall, ToolResult

SPEC = ToolSpec(
    name="queue_construction",
    description="Add buildings to the queue.",
    parameters={
        "type": "object",
        "properties": {"building": {"type": "string"}},
        "required": ["building"],
        "additionalProperties": False,
    },
)


def a_turn() -> list[Msg]:
    """The shape every real turn has: brief, tool call, result, next instruction."""
    return [
        Msg("user", text="## SWE -- 1936-01-01"),
        Msg(
            "assistant",
            text="Starting industry.",
            tool_calls=[ToolCall("call-1", "queue_construction", {"building": "civilian_factory"})],
        ),
        Msg(
            "user",
            tool_results=[ToolResult("call-1", '{"ok": true}', name="queue_construction")],
            text="Anything else?",
        ),
    ]


# --- Anthropic --------------------------------------------------------------

def test_anthropic_tool_calls_become_tool_use_blocks():
    wire = anthropic.wire_messages(a_turn())
    assistant = wire[1]
    assert assistant["role"] == "assistant"
    kinds = [block["type"] for block in assistant["content"]]
    assert kinds == ["text", "tool_use"]
    block = assistant["content"][1]
    assert block["id"] == "call-1" and block["input"] == {"building": "civilian_factory"}


def test_anthropic_tool_results_ride_on_a_user_message_before_new_text():
    wire = anthropic.wire_messages(a_turn())
    user = wire[2]
    assert user["role"] == "user"
    assert [block["type"] for block in user["content"]] == ["tool_result", "text"]
    assert user["content"][0]["tool_use_id"] == "call-1"


def test_anthropic_marks_failed_results_as_errors():
    messages = [Msg("user", tool_results=[ToolResult("c", "nope", is_error=True)])]
    block = anthropic.wire_messages(messages)[0]["content"][0]
    assert block["is_error"] is True


def test_anthropic_caches_the_tool_block_only_when_asked():
    cached = anthropic.wire_tools([SPEC], cache_prefix=True)
    assert cached[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in anthropic.wire_tools([SPEC], cache_prefix=False)[-1]
    assert cached[0]["input_schema"] == SPEC.parameters


def test_anthropic_emits_no_empty_messages():
    assert anthropic.wire_messages([Msg("assistant"), Msg("user")]) == []


# --- OpenAI-compatible ------------------------------------------------------

def test_openai_puts_the_system_prompt_first():
    wire = openai.wire_messages("SYSTEM", a_turn())
    assert wire[0] == {"role": "system", "content": "SYSTEM"}


def test_openai_tool_results_are_their_own_role_and_precede_new_user_text():
    """Ordering is the part that is easy to get wrong and invisible until a model
    starts ignoring results."""
    roles = [entry["role"] for entry in openai.wire_messages("s", a_turn())]
    assert roles == ["system", "user", "assistant", "tool", "user"]


def test_openai_serialises_tool_arguments_as_a_json_string():
    wire = openai.wire_messages("s", a_turn())
    call = wire[2]["tool_calls"][0]
    assert call["type"] == "function" and call["id"] == "call-1"
    assert json.loads(call["function"]["arguments"]) == {"building": "civilian_factory"}


def test_openai_tools_are_wrapped_in_a_function_envelope():
    wire = openai.wire_tools([SPEC])
    assert wire[0]["type"] == "function"
    assert wire[0]["function"]["parameters"] == SPEC.parameters
    assert openai.wire_tools([]) is None


# --- Gemini -----------------------------------------------------------------

def test_gemini_uses_model_not_assistant_as_the_role():
    contents = google.wire_contents(a_turn())
    assert [entry["role"] for entry in contents] == ["user", "model", "user"]


def test_gemini_tool_calls_and_results_are_parts():
    contents = google.wire_contents(a_turn())
    assert contents[1]["parts"][1]["function_call"] == {
        "name": "queue_construction",
        "args": {"building": "civilian_factory"},
    }
    response = contents[2]["parts"][0]["function_response"]
    assert response["name"] == "queue_construction"
    assert response["response"] == {"result": '{"ok": true}'}


def test_gemini_falls_back_to_the_call_id_when_a_result_has_no_name():
    messages = [Msg("user", tool_results=[ToolResult("call-9", "{}")])]
    part = google.wire_contents(messages)[0]["parts"][0]
    assert part["function_response"]["name"] == "call-9"


def test_gemini_declarations_match_the_catalog():
    assert google.wire_declarations([SPEC]) == [
        {"name": SPEC.name, "description": SPEC.description, "parameters": SPEC.parameters}
    ]
    assert google.wire_declarations(None) == []


# --- shared invariants ------------------------------------------------------

@pytest.mark.parametrize(
    "convert",
    [
        lambda m: anthropic.wire_messages(m),
        lambda m: openai.wire_messages("s", m)[1:],
        lambda m: google.wire_contents(m),
    ],
    ids=["anthropic", "openai", "gemini"],
)
def test_every_provider_round_trips_a_real_turn_without_losing_anything(convert):
    wire = json.dumps(convert(a_turn()))
    assert "call-1" in wire or "queue_construction" in wire
    assert "civilian_factory" in wire
    assert '{\\"ok\\": true}' in wire or '{"ok": true}' in wire


@pytest.mark.parametrize(
    "convert",
    [
        lambda specs: anthropic.wire_tools(specs),
        lambda specs: openai.wire_tools(specs),
        lambda specs: google.wire_declarations(specs),
    ],
    ids=["anthropic", "openai", "gemini"],
)
def test_the_whole_catalog_converts_for_every_provider(convert):
    specs = tool_specs()
    wire = convert(specs)
    assert len(wire) == len(specs)
    json.dumps(wire)          # must be serialisable; a schema object that is not is a bug
