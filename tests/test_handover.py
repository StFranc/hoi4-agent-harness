"""Action handover (#33): queue until the player grants control, re-check, report."""

import json

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval import run_scenario
from hoi4_harness.handover import QUEUE_FILENAME, HandoverQueue, stale_reason
from hoi4_harness.types import ActionCall, GameState, ResearchSlot


def _env(tmp_path) -> HOI4Env:
    env = HOI4Env(MockAdapter(), HarnessConfig(handover=True, run_dir=tmp_path))
    env.reset()
    return env


def test_actions_are_queued_not_executed_and_say_so(tmp_path):
    env = _env(tmp_path)
    result = env.act(ActionCall("set_national_focus", {"focus_id": "industrial_effort"}))
    assert result.ok and result.changed == {"queued": 1, "executed": False}
    assert "NOT done yet" in result.message
    assert env.read_state().national_focus is None
    queued = json.loads((tmp_path / QUEUE_FILENAME).read_text())
    assert queued[0]["action"] == "set_national_focus"
    assert "Queued for handover (1): #1 set_national_focus" in env.observe().brief


def test_notes_and_advance_time_are_not_queued(tmp_path):
    env = _env(tmp_path)
    assert env.act(ActionCall("note", {"text": "plan"})).message == "Noted."
    assert env.handover.pending == []


def test_nothing_runs_until_the_player_grants_a_window(tmp_path):
    env = _env(tmp_path)
    env.act(ActionCall("set_national_focus", {"focus_id": "industrial_effort"}))
    assert env.run_handover() is None
    assert env.read_state().national_focus is None

    env.handover.grant()
    report = env.run_handover()
    assert [r["action"] for r in report.ran] == ["set_national_focus"]
    assert env.read_state().national_focus == "industrial_effort"
    assert not env.handover.granted()            # handed back after the queue ran
    assert "Handover on 1936-01-01: 1 ran" in env.observe().brief
    assert "Handover on" not in env.observe().brief   # said once


def test_a_stale_action_is_reported_not_clicked(tmp_path):
    env = _env(tmp_path)
    env.act(ActionCall("set_national_focus", {"focus_id": "first"}))
    env.act(ActionCall("set_national_focus", {"focus_id": "second"}))
    env.handover.grant()
    report = env.run_handover()
    assert [r["arguments"]["focus_id"] for r in report.ran] == ["first"]
    assert report.stale[0]["reason"] == "'first' is already running"
    assert env.read_state().national_focus == "first"


def test_taking_control_back_stops_between_actions_and_keeps_the_rest(tmp_path):
    env = _env(tmp_path)
    for tech in ("a", "b", "c"):
        env.act(ActionCall("start_research", {"technology": tech}))
    env.handover.grant()
    applied = []

    def apply(call):
        applied.append(call)
        env.handover._release()          # the player grabs the keyboard mid-run
        return env._apply(call)

    report = env.handover.run(env.adapter.read_state, apply)
    assert len(applied) == 1 and report.aborted and report.left_queued == 2
    assert [p.call.arguments["technology"] for p in env.handover.pending] == ["b", "c"]


def test_a_failing_action_is_reported_as_failed(tmp_path):
    env = _env(tmp_path)
    env.act(ActionCall("hire_advisor", {"advisor_id": "x"}))   # 150 PP, mock starts with 25
    env.handover.grant()
    report = env.run_handover()
    assert report.failed and "Needs 150.0 PP" in report.failed[0]["reason"]


def test_stale_reasons_cover_research_and_production():
    busy = GameState(research=[ResearchSlot(0, "x", 10)])
    assert stale_reason(ActionCall("start_research", {"technology": "y"}), busy) == \
        "no research slot is free any more"
    full = GameState(military_factories=5)
    assert stale_reason(ActionCall("set_production", {"equipment": "e", "factories": 6}), full)


def test_the_loop_runs_the_queue_when_granted_and_records_it(tmp_path):
    config = HarnessConfig(handover=True, run_dir=tmp_path)
    env = HOI4Env(MockAdapter(), config)
    env.reset()
    call = ToolCall(id="1", name="set_national_focus", arguments={"focus_id": "industrial_effort"})
    planner = ScriptedClient(script=[
        LLMResponse(tool_calls=[call], stop_reason="tool_use", usage=Usage(10, 10)),
    ])
    HandoverQueue(tmp_path).grant()
    loop = AgentLoop(env, planner, config, transcript_path=tmp_path / "t.jsonl")
    loop.run(1)
    records = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    handover = next(r for r in records if r["kind"] == "handover")
    assert handover["ran"][0]["action"] == "set_national_focus"


def test_a_co_op_run_is_refused_by_the_eval_runner():
    with pytest.raises(ValueError, match="for playing, not scoring"):
        run_scenario("economy_ramp", HarnessConfig(handover=True, planner_enabled=False))


def test_the_handover_command_grants_and_releases(tmp_path, capsys):
    assert main(["handover", "--run-dir", str(tmp_path)]) == 0
    assert HandoverQueue(tmp_path).granted()
    assert main(["handover", "--run-dir", str(tmp_path), "--release"]) == 0
    assert not HandoverQueue(tmp_path).granted()


def test_handover_and_advisor_together_are_refused(tmp_path, capsys):
    assert main(["play", "--handover", "--advisor", "--run-dir", str(tmp_path)]) == 2


def test_the_same_action_asked_for_again_is_queued_once(tmp_path):
    """A reflex asks for the same thing every turn the queue waits; it must run once."""
    env = _env(tmp_path)
    call = ActionCall("queue_construction", {"building": "civilian_factory", "state": "capital",
                                              "count": 2})
    first = env.act(call)
    again = env.act(ActionCall(call.name, dict(call.arguments)))
    assert first.changed["queued"] == again.changed["queued"] == 1
    assert "Already queued as #1" in again.message
    assert len(env.handover.pending) == 1
