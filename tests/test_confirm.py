"""The confirmation gate's operator UI.

Three promises, and the tests hold them one at a time: the prompt shows enough to
judge the call, every decision reaches the transcript -- denials included, since
a veto is a fact about the agent -- and a live run carries on sensibly whether
the answer is yes or no.
"""

from __future__ import annotations

import json
from pathlib import Path

from hoi4_harness import cli
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop, Transcript
from hoi4_harness.config import HarnessConfig
from hoi4_harness.confirm import Confirmation, TerminalConfirmer
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall

WAR = ActionCall(
    name="diplomacy",
    arguments={"action": "declare_war", "target": "FRA"},
    call_id="c1",
    rationale="France is weakly defended and the window is closing.",
)


class WarCapableMock(MockAdapter):
    """A mock that claims it can declare war, so the gate is reachable."""

    supported_actions = MockAdapter.supported_actions | {"diplomacy"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.declared: list[ActionCall] = []

    def _do_diplomacy(self, call: ActionCall):
        self.declared.append(call)
        return self._ok(call, "War declared.")


def answering(*lines: str) -> TerminalConfirmer:
    """A confirmer whose answers are scripted, so no test needs a tty."""
    answers = iter(lines)
    return TerminalConfirmer(Transcript(None), input_fn=lambda _prompt: next(answers))


def declaring_war(transcript: Path) -> AgentLoop:
    """One live wake whose model opens with a war declaration, then waits."""
    config = HarnessConfig(
        adapter="mock", dry_run=False, require_confirmation=True, days_per_turn=7
    )
    env = HOI4Env(WarCapableMock(seed=3), config)
    env.reset()
    script = [
        LLMResponse(
            text="France is weakly defended and the window is closing.",
            tool_calls=[
                ToolCall("1", "diplomacy", {"action": "declare_war", "target": "FRA"}),
                ToolCall("2", "advance_time", {"days": 7}),
            ],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop = AgentLoop(
        env=env,
        planner=ScriptedClient(script=script),
        config=config,
        transcript_path=transcript,
    )
    return loop


def records_from(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the prompt --------------------------------------------------------------


def test_the_prompt_shows_the_action_its_arguments_the_rationale_and_the_date(capsys):
    confirmer = answering("y")
    assert confirmer(Confirmation(call=WAR, date="1936-04-11", turn=7)) is True
    err = capsys.readouterr().err
    assert "[confirm 1936-04-11 | turn 7]" in err
    assert 'diplomacy(action="declare_war", target="FRA")' in err
    assert "France is weakly defended and the window is closing." in err
    assert "always = every 'diplomacy:declare_war' this session" in err


def test_a_model_that_said_nothing_still_gets_a_line_for_it(capsys):
    answering("n")(Confirmation(call=ActionCall("diplomacy", {"action": "declare_war"})))
    assert "(the model gave no rationale)" in capsys.readouterr().err


def test_it_reads_the_answer_off_the_terminal_without_touching_stdout(capsys):
    prompts = []
    confirmer = TerminalConfirmer(
        Transcript(None), input_fn=lambda prompt: (prompts.append(prompt), "y")[1]
    )
    confirmer(Confirmation(call=WAR, date="1936-04-11"))
    out, err = capsys.readouterr()
    # The question goes to stderr and input() is handed no prompt, so play's
    # stdout stays parseable JSON.
    assert prompts == [""]
    assert out == ""
    assert "irreversible" in err


def test_an_empty_or_unrecognised_answer_is_a_no(capsys):
    assert answering("")(Confirmation(call=WAR)) is False
    assert answering("maybe")(Confirmation(call=WAR)) is False
    assert "reading it as no" in capsys.readouterr().err


def test_exhausted_input_is_a_no_rather_than_a_hang_or_a_crash(capsys):
    def no_more(_prompt: str) -> str:
        raise EOFError

    assert TerminalConfirmer(Transcript(None), input_fn=no_more)(Confirmation(call=WAR)) is False
    assert "no input: reading it as no" in capsys.readouterr().err


# --- always ------------------------------------------------------------------


def test_always_answers_the_same_scope_from_then_on(tmp_path):
    confirmer = answering("a")
    confirmer.transcript = Transcript(tmp_path / "t.jsonl")
    assert confirmer(Confirmation(call=WAR, date="1936-04-11", turn=1)) is True
    # A second identical call is not asked about: input_fn would run out of
    # scripted answers and raise if it were.
    assert confirmer(Confirmation(call=WAR, date="1936-05-01", turn=5)) is True
    decisions = records_from(tmp_path / "t.jsonl")
    assert [d["prompted"] for d in decisions] == [True, False]


def test_always_covers_one_mode_of_a_verb_not_the_whole_verb(tmp_path):
    confirmer = answering("a", "n")
    confirmer.transcript = Transcript(tmp_path / "t.jsonl")
    assert confirmer(Confirmation(call=WAR)) is True
    lend_lease = ActionCall("diplomacy", {"action": "lend_lease", "target": "FIN"})
    # Saying "always" to a war must not pre-approve every later diplomacy call.
    assert confirmer(Confirmation(call=lend_lease)) is False


def test_the_scope_names_the_mode_when_the_action_has_one():
    assert Confirmation(call=WAR).scope == "diplomacy:declare_war"
    order = ActionCall("set_army_order", {"army": "1st", "order": "naval_invasion"})
    assert Confirmation(call=order).scope == "set_army_order:naval_invasion"
    # No enum argument to narrow it by: the action name is the whole key.
    assert Confirmation(call=ActionCall("clear_ai_directives", {})).scope == "clear_ai_directives"


# --- the record --------------------------------------------------------------


def test_every_decision_is_recorded_including_the_denials(tmp_path):
    transcript = Transcript(tmp_path / "t.jsonl")
    confirmer = TerminalConfirmer(transcript, input_fn=lambda _prompt: "n")
    confirmer(Confirmation(call=WAR, date="1936-04-11", turn=7))
    confirmer = TerminalConfirmer(transcript, input_fn=lambda _prompt: "y")
    confirmer(Confirmation(call=WAR, date="1936-04-12", turn=8))

    decisions = [r for r in records_from(tmp_path / "t.jsonl") if r["kind"] == "confirmation"]
    assert [d["decision"] for d in decisions] == ["denied", "approved"]
    assert [d["answer"] for d in decisions] == ["no", "yes"]
    first = decisions[0]
    assert first["date"] == "1936-04-11" and first["turn"] == 7
    assert first["name"] == "diplomacy"
    assert first["arguments"] == {"action": "declare_war", "target": "FRA"}
    assert first["rationale"] == WAR.rationale
    assert first["scope"] == "diplomacy:declare_war"


def test_the_env_hands_the_hook_the_date_turn_and_rationale():
    seen: list[Confirmation] = []
    env = HOI4Env(WarCapableMock(seed=3), HarnessConfig(dry_run=False))
    env.turn = 4
    env.confirm_hook = lambda request: (seen.append(request), True)[1]
    env.act(WAR, date="1936-04-11")
    (request,) = seen
    assert isinstance(request, Confirmation)
    assert request.date == "1936-04-11"
    assert request.turn == 4
    assert request.rationale == WAR.rationale
    assert request.call.call_id == "c1"


# --- the run -----------------------------------------------------------------


def test_a_live_run_asks_before_a_war_and_a_no_leaves_the_game_alone(tmp_path):
    transcript = tmp_path / "run.jsonl"
    loop = declaring_war(transcript)
    loop.env.confirm_hook = TerminalConfirmer(loop.transcript, input_fn=lambda _prompt: "n")
    report = loop.run(1)

    assert loop.env.adapter.declared == []       # the action never reached the game
    assert report.turns == 1                     # and the run carried on
    assert report.stopped_reason is None
    decisions = [r for r in records_from(transcript) if r["kind"] == "confirmation"]
    assert [d["decision"] for d in decisions] == ["denied"]
    # The model was told plainly, so it can try something else next turn.
    actions = [r for r in records_from(transcript) if r["kind"] == "actions"]
    assert any(a["is_error"] for record in actions for a in record["results"])


def test_a_live_run_carries_out_the_war_when_the_answer_is_yes(tmp_path):
    transcript = tmp_path / "run.jsonl"
    loop = declaring_war(transcript)
    loop.env.confirm_hook = TerminalConfirmer(loop.transcript, input_fn=lambda _prompt: "y")
    report = loop.run(1)

    assert [c.name for c in loop.env.adapter.declared] == ["diplomacy"]
    assert report.actions_ok == 2                # the declaration and the advance
    assert [r["decision"] for r in records_from(transcript)
            if r["kind"] == "confirmation"] == ["approved"]


def test_a_run_without_the_prompt_is_left_unattended(tmp_path):
    loop = declaring_war(tmp_path / "run.jsonl")
    loop.env.confirm_hook = None                 # the default: no hook, no question
    report = loop.run(1)
    assert [c.name for c in loop.env.adapter.declared] == ["diplomacy"]
    assert report.stopped_reason is None


# --- the flag ----------------------------------------------------------------


def test_play_confirm_installs_the_prompt_and_nothing_does_without_it(tmp_path, monkeypatch):
    built: list[object] = []

    class Spy(TerminalConfirmer):
        def __init__(self, transcript):
            super().__init__(transcript)
            built.append(self)

    monkeypatch.setattr(cli, "TerminalConfirmer", Spy)
    args = ["play", "--turns", "1", "--adapter", "mock", "--provider", "scripted",
            "--live", "--run-dir", str(tmp_path / "run")]
    assert cli.main(args) == 0
    assert built == []
    assert cli.main([*args, "--confirm"]) == 0
    assert len(built) == 1
