"""A run has to survive the things that happen during a six-hour campaign:
a rate limit, a 500, a crash, a reboot.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.errors import classify, describe
from hoi4_harness.agent.llm.base import LLMClient, LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.resume import rebuild
from hoi4_harness.config import HarnessConfig, LLMConfig
from hoi4_harness.env import HOI4Env


class Boom(Exception):
    """Stands in for an SDK exception carrying an HTTP status."""

    def __init__(self, message="boom", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FlakyClient(LLMClient):
    """Fails its first `failures` calls, then behaves."""

    def __init__(self, failures: int, exc: Exception | None = None):
        self.remaining = failures
        self.exc = exc or Boom("rate limited", 429)
        self.inner = ScriptedClient()
        self.calls = 0

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc
        return self.inner.complete(system, messages, tools, max_tokens)


def build(tmp_path, planner, name="run", **overrides):
    config = HarnessConfig(adapter="mock", planner=LLMConfig(provider="scripted"), **overrides)
    env = HOI4Env(MockAdapter(seed=5), config)
    env.reset()
    return AgentLoop(
        env=env, planner=planner, config=config,
        transcript_path=tmp_path / f"{name}.jsonl",
    )


# --- #25 classification -----------------------------------------------------

@pytest.mark.parametrize("status", [429, 500, 502, 503, 529, 408])
def test_retryable_statuses_are_transient(status):
    assert classify(Boom("x", status)) == "transient"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_fatal(status):
    assert classify(Boom("x", status)) == "fatal"


def test_classification_falls_back_to_the_exception_name():
    assert classify(type("RateLimitError", (Exception,), {})()) == "transient"
    assert classify(type("AuthenticationError", (Exception,), {})()) == "fatal"
    assert classify(TimeoutError("slow")) == "transient"


def test_an_unrecognised_error_is_transient_because_a_wrong_fatal_costs_the_run():
    assert classify(Exception("something new")) == "transient"


def test_describe_includes_the_status_and_type():
    assert "HTTP 429" in describe(Boom("slow down", 429))
    assert "Boom" in describe(Boom("slow down", 429))


# --- #25 loop behaviour -----------------------------------------------------

def test_a_transient_failure_costs_a_turn_not_the_run(tmp_path):
    loop = build(tmp_path, FlakyClient(failures=1))
    report = loop.run(6)
    assert report.turns == 6                     # the campaign continued
    assert report.llm_errors == 1
    assert report.stopped_reason is None
    assert report.start_date < report.end_date   # and the clock kept moving


def test_the_failed_turn_is_played_by_the_reflex_layer(tmp_path):
    loop = build(tmp_path, FlakyClient(failures=1))
    loop.run(3)
    records = [json.loads(x) for x in loop.transcript.path.read_text(encoding="utf-8").splitlines()]
    errors = [r for r in records if r["kind"] == "llm_error"]
    assert len(errors) == 1 and errors[0]["error_kind"] == "transient"
    assert any(r["kind"] in {"reflex", "skip"} for r in records)


def test_a_fatal_error_stops_the_run_immediately(tmp_path):
    loop = build(tmp_path, FlakyClient(failures=99, exc=Boom("bad key", 401)))
    report = loop.run(20)
    assert report.turns == 1                     # not twenty turns of nothing
    assert "fatal" in report.stopped_reason
    assert report.llm_errors == 1


def test_enough_consecutive_transients_also_stop_the_run(tmp_path):
    loop = build(tmp_path, FlakyClient(failures=99), max_consecutive_llm_errors=3)
    report = loop.run(20)
    assert report.llm_errors == 3
    assert "consecutive" in report.stopped_reason


def test_the_error_counter_resets_after_a_success(tmp_path):
    loop = build(tmp_path, FlakyClient(failures=2), max_consecutive_llm_errors=3)
    report = loop.run(10)
    assert report.stopped_reason is None
    assert report.llm_errors == 2


# --- #26 resume -------------------------------------------------------------

def note_response(text):
    return LLMResponse(
        tool_calls=[ToolCall("1", "note", {"text": text})],
        stop_reason="tool_use",
        usage=Usage(100, 20),
    )


def test_a_transcript_rebuilds_into_what_the_run_knew(tmp_path):
    loop = build(tmp_path, ScriptedClient(script=[note_response("Build civs until 1938.")]))
    report = loop.run(5)

    state = rebuild(loop.transcript.path)
    assert state.planner_calls == report.planner_calls
    assert state.usage.input_tokens == report.spend["input_tokens"]
    assert state.turns == report.turns
    assert any("Build civs until 1938." in e.text for e in state.memory.journal)


def test_a_transcript_truncated_mid_write_still_rebuilds(tmp_path):
    loop = build(tmp_path, ScriptedClient())
    loop.run(6)
    path = loop.transcript.path

    # Simulate dying mid-write: keep every complete record, then half of one more.
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][: len(lines[-1]) // 2],
                    encoding="utf-8")

    state = rebuild(path)
    assert state.truncated
    assert state.records == len(lines) - 1        # everything before the tear is kept
    assert state.planner_calls > 0


def test_rebuilding_a_missing_transcript_is_empty_not_an_error(tmp_path):
    state = rebuild(tmp_path / "never-existed.jsonl")
    assert state.records == 0 and state.planner_calls == 0


def test_resuming_carries_spend_forward_rather_than_doubling_the_budget(tmp_path):
    first = build(tmp_path, ScriptedClient(), name="campaign")
    first_report = first.run(5)

    second = AgentLoop(
        env=HOI4Env(MockAdapter(seed=5), first.config),
        planner=ScriptedClient(),
        config=first.config,
        transcript_path=first.transcript.path,
        resume=True,
    )
    assert second.report.planner_calls == first_report.planner_calls
    assert second.budget.spend.usage.input_tokens == first_report.spend["input_tokens"]

    second_report = second.run(3)
    assert second_report.planner_calls > first_report.planner_calls
    assert second_report.resumed_from


def test_resuming_appends_to_the_transcript_instead_of_truncating_it(tmp_path):
    first = build(tmp_path, ScriptedClient(), name="campaign")
    first.run(4)
    before = len(first.transcript.path.read_text(encoding="utf-8").splitlines())

    second = AgentLoop(
        env=HOI4Env(MockAdapter(seed=5), first.config),
        planner=ScriptedClient(),
        config=first.config,
        transcript_path=first.transcript.path,
        resume=True,
    )
    second.run(2)
    after = len(second.transcript.path.read_text(encoding="utf-8").splitlines())
    assert after > before


def test_memory_is_checkpointed_every_turn_not_at_the_end(tmp_path):
    loop = build(tmp_path, ScriptedClient(script=[note_response("Rush industry.")]))
    loop.run(2)
    saved = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    assert any("Rush industry." in entry["text"] for entry in saved["journal"])
