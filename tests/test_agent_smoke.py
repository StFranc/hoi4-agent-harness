"""End-to-end invariants for a whole agent run.

These are the tests CI leans on. They do not check that the agent plays *well* --
the scripted policy plays badly on purpose -- they check that the machinery
around it is honest: the clock moves, every tool call gets a verdict, the
catalog and the adapters agree, spend is accounted for, and two identical runs
produce identical transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import BudgetConfig, HarnessConfig, LLMConfig
from hoi4_harness.env import HOI4Env

TURNS = 25


def run(tmp_path: Path, name: str = "run", **overrides):
    overrides.setdefault("budget", BudgetConfig(usd_per_m_input=5.0, usd_per_m_output=25.0))
    config = HarnessConfig(
        adapter="mock",
        planner=LLMConfig(provider="scripted"),
        turns=TURNS,
        **overrides,
    )
    env = HOI4Env(MockAdapter(seed=config.seed, country=config.country, start=config.start_date),
                  config)
    env.reset()
    transcript = tmp_path / f"{name}.jsonl"
    loop = AgentLoop(env=env, planner=ScriptedClient(), config=config, transcript_path=transcript)
    report = loop.run(TURNS)
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    return loop, env, report, records


def test_the_clock_always_moves_forward(tmp_path):
    _, _, report, _ = run(tmp_path)
    assert report.turns == TURNS
    assert report.start_date < report.end_date


def test_every_tool_call_gets_a_verdict(tmp_path):
    """No silent drops: each tool call the model made produced a result."""
    _, _, _, records = run(tmp_path)
    called = sum(len(r["tool_calls"]) for r in records if r["kind"] == "llm")
    resolved = sum(len(r["results"]) for r in records if r["kind"] == "actions")
    assert called > 0
    assert called == resolved


def test_the_agent_never_produces_a_call_the_catalog_rejects(tmp_path):
    """A schema or capability error here means the harness contradicts itself."""
    _, _, _, records = run(tmp_path)
    bad = [
        result
        for record in records
        if record["kind"] == "actions"
        for result in record["results"]
        if result["is_error"] and '"error": "invalid' in result["content"]
    ]
    assert bad == [], bad


def test_spend_is_accounted_for_on_every_model_call(tmp_path):
    loop, _, report, records = run(tmp_path)
    llm_records = [r for r in records if r["kind"] == "llm"]
    assert len(llm_records) == report.planner_calls
    assert all("usage" in r for r in llm_records)
    assert report.spend["input_tokens"] > 0
    assert report.spend["usd"] > 0        # priced with the rates set above
    assert report.spend["blocked"] is None


def test_two_identical_runs_produce_identical_transcripts(tmp_path):
    _, _, first, first_records = run(tmp_path, "a")
    _, _, second, second_records = run(tmp_path, "b")
    assert first.to_dict() | {"transcript_path": None} == second.to_dict() | {
        "transcript_path": None
    }
    # Wall-clock time and the output path are the only things allowed to differ.
    volatile = {"t", "transcript_path"}

    def strip(records):
        return [{k: v for k, v in r.items() if k not in volatile} for r in records]

    assert strip(first_records) == strip(second_records)


def test_a_budget_ceiling_is_never_exceeded(tmp_path):
    loop, _, report, _ = run(tmp_path, "budget", budget=BudgetConfig(max_llm_calls=3))
    assert loop.budget.spend.calls <= 3
    assert report.planner_calls <= 3
    assert report.turns == TURNS          # the run continued without the model
    assert loop.budget.why_blocked()


@pytest.mark.parametrize("mode", ["llm", "ai"])
def test_both_control_modes_complete_a_run(tmp_path, mode):
    _, env, report, _ = run(tmp_path, f"mode-{mode}", operational_control=mode)
    assert report.turns == TURNS
    from hoi4_harness.agent.hybrid import DIRECTIVE_ACTIONS, OPERATIONAL_ACTIONS

    forbidden = OPERATIONAL_ACTIONS if mode == "ai" else DIRECTIVE_ACTIONS
    assert env.allowed_actions & forbidden == set()


def test_the_transcript_is_complete_and_well_formed(tmp_path):
    _, _, _, records = run(tmp_path)
    kinds = {r["kind"] for r in records}
    assert {"observe", "llm", "actions", "run_end"} <= kinds
    assert records[-1]["kind"] == "run_end"
    assert all(isinstance(r.get("t"), float) for r in records)
