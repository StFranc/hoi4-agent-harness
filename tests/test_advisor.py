"""Advisor mode: the model recommends and nothing executes.

The tests hold the two promises the feature makes. First, a run in advisor mode
cannot change the game -- asserted on adapter state, never on a flag. Second,
the model is never left believing it acted: the tool result says so in plain
words, and the note it wrote still comes back to it.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hoi4_harness.adapters.base import AdapterInfo, GameAdapter
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, ActionResult, GameState


def make_loop(script=None, adapter=None, transcript_path=None, **overrides):
    config = HarnessConfig(adapter="mock", advisor=True, **overrides)
    adapter = adapter if adapter is not None else MockAdapter(seed=3)
    env = HOI4Env(adapter, config)
    env.reset()
    loop = AgentLoop(
        env=env,
        planner=ScriptedClient(script=script),
        config=config,
        transcript_path=transcript_path,
    )
    return loop, env


def a_turn_of_advice(tag: str) -> LLMResponse:
    """One scripted wake proposing what a real first week would propose.

    The default scripted policy keys off markers in the brief, and the first
    brief of a run is a delta that carries none of them -- so these tests name
    their calls explicitly instead. The note rides along because it is the one
    call advisor mode processes rather than intercepts.
    """
    return LLMResponse(
        tool_calls=[
            ToolCall(f"{tag}-focus", "set_national_focus", {"focus_id": "industrial_effort"}),
            ToolCall(f"{tag}-research", "start_research", {"technology": "construction1"}),
            ToolCall(f"{tag}-build", "queue_construction",
                     {"building": "civilian_factory", "state": "capital", "count": 2}),
            ToolCall(f"{tag}-note", "note", {"text": f"Week {tag}: keep building toward 1938."}),
            ToolCall(f"{tag}-wait", "advance_time", {"days": 7}),
        ],
        stop_reason="tool_use",
        usage=Usage(10, 10),
    )


def records_from(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_an_advisor_run_executes_zero_actions(tmp_path):
    script = [a_turn_of_advice(str(i)) for i in range(6)]
    loop, env = make_loop(script=script, transcript_path=tmp_path / "advisor.jsonl")
    loop.run(6)
    adapter = env.adapter
    # Six wakes asked for a focus, research, construction and an advance.
    # None of it may reach the game.
    assert adapter.state.national_focus is None
    assert all(slot.technology is None for slot in adapter.state.research)
    assert adapter.state.construction == []
    assert adapter.state.divisions[0].count == 12


def test_the_tool_result_says_the_call_was_not_executed(tmp_path):
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "set_national_focus", {"focus_id": "industrial_effort"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(stop_reason="end_turn", usage=Usage(10, 10)),
    ]
    loop, env = make_loop(script=script)
    loop.run(1)
    # The model's next round is what carries the verdict back to it.
    messages = loop.planner.calls[1][1]
    returned = [result for msg in messages for result in msg.tool_results]
    assert len(returned) == 1
    assert returned[0].is_error is False          # not a rejection: nothing to fix
    payload = json.loads(returned[0].content)
    assert payload["executed"] is False
    assert "recommendation" in payload["message"]
    assert "NOT executed" in payload["message"]


def test_a_note_reaches_the_journal_and_the_next_turn_prompt(tmp_path):
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "note", {"text": "Build civs until 1938."})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(
            tool_calls=[ToolCall("2", "advance_time", {"days": 7})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
    ]
    loop, env = make_loop(script=script)
    loop.run(2)
    assert any(e.text == "Build civs until 1938." for e in loop.memory.journal)
    # The third model call is turn 2's prompt; the note rides in the memory block.
    turn2_messages = loop.planner.calls[2][1]
    assert any("Build civs until 1938." in msg.text for msg in turn2_messages)


def test_recommendations_land_in_the_transcript_with_the_game_date(tmp_path):
    transcript = tmp_path / "advisor.jsonl"
    script = [a_turn_of_advice(str(i)) for i in range(6)]
    loop, env = make_loop(script=script, transcript_path=transcript)
    loop.run(6)
    records = records_from(transcript)
    recommendations = [r for r in records if r["kind"] == "recommendation"]
    assert recommendations, "no recommendations were recorded"
    assert all(r["date"].startswith("1936-") for r in recommendations)
    assert all(isinstance(r["turn"], int) for r in recommendations)
    assert any(r["name"] == "queue_construction" for r in recommendations)
    # Every recommendation is dated against a brief the harness actually sent.
    observes = {r["date"] for r in records if r["kind"] == "observe"}
    assert {r["date"] for r in recommendations} <= observes


class ReadOnlyAdapter(GameAdapter):
    """Stands in for logtail/savegame: it sees the campaign but can do nothing."""

    supported_actions = frozenset()

    def __init__(self):
        self.date = "1936-01-01"
        self.applied: list[ActionCall] = []

    def info(self) -> AdapterInfo:
        return AdapterInfo(name="read-only", readable=True, writable=False, clock_control=False)

    def read_state(self) -> GameState:
        return GameState(date=self.date, country="SWE", political_power=10.0)

    def apply(self, call: ActionCall) -> ActionResult:
        self.applied.append(call)   # recorded so a test can prove it is never reached
        return ActionResult(
            ok=False, action=call.name, message="read-only adapter cannot act",
            error_kind="unsupported",
        )

    def advance(self, days: int) -> GameState:
        self.date = (dt.date.fromisoformat(self.date) + dt.timedelta(days=days)).isoformat()
        return GameState(date=self.date, country="SWE", political_power=10.0)


def test_it_works_with_an_adapter_that_supports_no_actions(tmp_path):
    adapter = ReadOnlyAdapter()
    script = [a_turn_of_advice(str(i)) for i in range(3)]
    loop, env = make_loop(adapter=adapter, script=script,
                          transcript_path=tmp_path / "advisor.jsonl")
    report = loop.run(3)
    # Interception happens before the adapter is ever asked to do anything.
    assert adapter.applied == []
    assert report.actions_ok == 0 and report.actions_failed == 0
    # The one exception still works: notes are processed into the journal even
    # though this adapter supports no actions at all.
    assert any("keep building toward" in e.text for e in loop.memory.journal)
    recommendations = [r for r in records_from(tmp_path / "advisor.jsonl")
                       if r["kind"] == "recommendation"]
    assert recommendations


def test_the_clock_still_advances():
    loop, env = make_loop()
    report = loop.run(6)
    span = dt.date.fromisoformat(report.end_date) - dt.date.fromisoformat(report.start_date)
    # Only the harness moves the clock -- the model's advance_time calls are
    # intercepted too -- so the span is exactly turns x days_per_turn.
    assert span == dt.timedelta(days=6 * loop.config.days_per_turn)


def test_the_env_itself_refuses_to_act_in_advisor_mode():
    """The loop routes around env.act, but the env refusing is what makes the
    mode structural: no call site can act in advisor mode by accident."""
    env = HOI4Env(MockAdapter(seed=3), HarnessConfig(advisor=True))
    env.reset()
    result = env.act(
        ActionCall(name="queue_construction",
                   arguments={"building": "civilian_factory", "state": "capital"})
    )
    assert not result.ok
    assert result.error_kind == "not_executed"
    assert env.adapter.state.construction == []


def test_recommendations_are_printed_for_the_player(capsys):
    loop, env = make_loop(script=[a_turn_of_advice("1")])
    loop.run(1)
    out = capsys.readouterr().out
    assert "[advisor 1936-01-01 | turn 0]" in out
    assert "no national focus is running" in out   # why the model was woken
    assert 'would: set_national_focus(focus_id="industrial_effort")' in out
    assert 'would: queue_construction(building="civilian_factory", state="capital", count=2)' in out
    assert "would: advance_time(days=7)" in out
