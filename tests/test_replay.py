import json

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.replay import ReplayError, list_turns, load, replay_turn


def run_transcript(tmp_path, script=None, turns=3):
    """A real run against the mock adapter, transcript and all."""
    config = HarnessConfig(adapter="mock")
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    client = ScriptedClient(script=script)
    loop = AgentLoop(
        env=env,
        planner=client,
        config=config,
        transcript_path=tmp_path / "transcript.jsonl",
    )
    loop.run(turns)
    return tmp_path / "transcript.jsonl", client


def planner_turns(path):
    """The turns that actually woke the planner.

    Not every turn does, and which ones do depends on how much the agent fixed
    earlier -- so tests must read this from the transcript rather than assume
    turn 1 exists.
    """
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a transcript may end mid-write; that is a supported input
        if record.get("kind") == "observe":
            turns.append(int(record["turn"]))
    return turns


def call_index(path, turn):
    """Where client.calls holds the first round of ``turn``: the loop writes one
    llm record per complete() call, so count them up to the observe record."""
    seen = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("kind") == "observe":
            if record.get("turn") == turn:
                return seen
        elif record.get("kind") == "llm":
            seen += 1
    raise AssertionError(f"turn {turn} not found in {path}")


def test_replay_rebuilds_the_prompt_the_original_model_saw(tmp_path):
    path, client = run_transcript(tmp_path)
    config = HarnessConfig(adapter="mock")
    for turn in planner_turns(path):
        outcome = replay_turn(path, turn, config)
        system, messages = client.calls[call_index(path, turn)]
        assert outcome.prompt.system == system
        assert outcome.prompt.user == messages[0].text


def test_the_tool_menu_is_filtered_like_a_live_run(tmp_path):
    path, _ = run_transcript(tmp_path)
    outcome = replay_turn(path, 0, HarnessConfig(adapter="mock"))
    offered = {spec.name for spec in outcome.prompt.tools}
    assert "advance_time" in offered              # the mock supports it
    assert "delegate_army_to_ai" not in offered   # the llm control mode drops directives


def test_a_note_from_an_earlier_turn_comes_back_in_the_rebuilt_prompt(tmp_path):
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "note", {"text": "Build civs until 1938."})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    path, _ = run_transcript(tmp_path, script=script)
    later = planner_turns(path)[-1]
    outcome = replay_turn(path, later, HarnessConfig(adapter="mock"))
    assert "Build civs until 1938." in outcome.prompt.user   # the journal
    assert "Recent turns:" in outcome.prompt.user            # the digest is rebuilt
    assert "note" in outcome.prompt.user.split("Recent turns:")[1]


def test_list_shows_the_turns_with_dates_and_wake_reasons(tmp_path):
    path, _ = run_transcript(tmp_path, turns=2)
    table = list_turns(path)
    assert "1936-01-01" in table
    # The dates are whatever the run produced; the table must show them.
    dates = [
        json.loads(line)["date"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "observe"
    ]
    for date in dates:
        assert date in table
    assert "no national focus is running" in table


def test_a_turn_that_does_not_exist_is_a_clear_error(tmp_path):
    path, _ = run_transcript(tmp_path, turns=2)
    present = ", ".join(str(turn) for turn in planner_turns(path))
    with pytest.raises(ReplayError, match=rf"no turn 99.*{present}"):
        replay_turn(path, 99, HarnessConfig(adapter="mock"))


def test_a_transcript_with_no_planner_turns_is_a_clear_error(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ReplayError, match="no planner turns"):
        list_turns(empty)


def test_a_truncated_or_malformed_line_is_tolerated(tmp_path):
    path, _ = run_transcript(tmp_path, turns=2)
    intact = len(planner_turns(path))
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw + '{"t": 1.0, "kind": "obse\n\nnot json at all\n', encoding="utf-8")
    last = planner_turns(path)[-1]
    outcome = replay_turn(path, last, HarnessConfig(adapter="mock"))
    assert outcome.record["turn"] == last
    assert sum(1 for r in load(path) if r.get("kind") == "observe") == intact


def test_the_new_calls_are_printed_not_executed(tmp_path):
    path, _ = run_transcript(tmp_path, turns=1)
    before = path.read_text(encoding="utf-8")
    war = ScriptedClient(
        script=[
            LLMResponse(
                text="War it is.",
                tool_calls=[ToolCall("x", "diplomacy", {"action": "declare_war", "target": "GER"})],
                stop_reason="tool_use",
                usage=Usage(5, 5),
            )
        ]
    )
    outcome = replay_turn(path, 0, HarnessConfig(adapter="mock"), planner=war)
    rendered = outcome.render()
    assert 'diplomacy(action="declare_war", target="GER")' in rendered
    assert "War it is." in rendered
    assert path.read_text(encoding="utf-8") == before  # the transcript was only ever read


def test_the_cli_prints_the_comparison_and_exits_zero(tmp_path, capsys):
    path, _ = run_transcript(tmp_path, turns=1)
    code = main(["replay", str(path), "--turn", "0"])
    out = capsys.readouterr().out
    assert code == 0
    assert "original (scripted)" in out
    assert "replayed (scripted:scripted)" in out
    # Whatever the original turn called must appear; which action that is depends
    # on what the brief showed, so read it from the transcript.
    first_call = next(
        json.loads(line)["tool_calls"][0]["name"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "llm"
    )
    assert first_call in out


def test_long_calls_wrap_instead_of_breaking_the_columns(tmp_path):
    path, _ = run_transcript(tmp_path, turns=1)
    rambling = ScriptedClient(
        script=[
            LLMResponse(
                tool_calls=[ToolCall("x", "note", {"text": "intent " * 40})],
                stop_reason="tool_use",
                usage=Usage(5, 5),
            )
        ]
    )
    rows = replay_turn(path, 0, HarnessConfig(adapter="mock"), planner=rambling).render().splitlines()
    separator = next(i for i, r in enumerate(rows) if r.startswith("  --"))
    body = rows[separator - 1:]
    for row in body:
        if not row.startswith("  "):  # the footer starts without the two-space indent
            break
        assert row.index(" | ") == 54  # 2-space gutter + 52-char column, every row
    assert "intent" in "\n".join(body)  # wrapped, not truncated


def test_a_misconfigured_provider_is_a_clear_error(tmp_path, capsys, monkeypatch):
    path, _ = run_transcript(tmp_path, turns=1)
    monkeypatch.setenv("HOI4_LLM_PROVIDER", "nope")
    assert main(["replay", str(path), "--turn", "0"]) == 1
    err = capsys.readouterr().err
    assert "nope" in err
    assert "Traceback" not in err


def test_the_cli_lists_turns_and_reports_errors_without_a_traceback(tmp_path, capsys):
    path, _ = run_transcript(tmp_path, turns=1)

    assert main(["replay", str(path), "--list"]) == 0
    assert "1936-01-01" in capsys.readouterr().out

    assert main(["replay", str(path), "--turn", "5"]) == 1
    err = capsys.readouterr().err
    assert "no turn 5" in err
    assert "Traceback" not in err

    assert main(["replay", str(tmp_path / "missing.jsonl"), "--list"]) == 1
    assert "cannot read" in capsys.readouterr().err

    assert main(["replay", str(path)]) == 2
    assert "--turn" in capsys.readouterr().err
