"""The reflex-only baseline: what the harness scores with no model at all.

Every score is reported as a delta over this, so these tests pin what makes the
number trustworthy: a --no-llm run truly makes zero model calls, the committed
file still describes the scenarios it was measured against, and a baseline that
no longer matches is reported as ignored rather than silently compared against.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness import __version__
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm import LLMClient
from hoi4_harness.agent.llm.base import LLMResponse
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval import SCENARIOS, run_scenario
from hoi4_harness.eval.baselines import BASELINE_PATH, for_scenario, load, write_baselines
from hoi4_harness.eval.metrics import ScoreCard


def reflex_only(**overrides) -> HarnessConfig:
    return HarnessConfig(adapter="mock", planner_enabled=False, **overrides)


def make_loop(planner, **overrides):
    config = reflex_only(turns=6, **overrides)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=planner, config=config)


class ExplodingPlanner(LLMClient):
    """A planner that fails the test the moment it is woken."""

    name = "exploding"

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        raise AssertionError("the planner must never be woken in a reflex-only run")


def test_a_no_llm_run_makes_zero_model_calls_and_still_advances_the_clock():
    loop = make_loop(planner=None)
    report = loop.run(6)

    assert report.planner_calls == 0
    assert loop.budget.spend.calls == 0
    assert loop.budget.summary()["input_tokens"] == 0
    assert report.reflex_turns == 6              # every turn took the free path
    assert report.turns == 6
    assert report.start_date < report.end_date   # and the calendar still moved


def test_a_supplied_planner_is_never_woken_when_the_planner_is_disabled():
    # --no-llm must not depend on the caller remembering to drop the client.
    loop = make_loop(planner=ExplodingPlanner())
    loop.run(6)                                  # no exception: complete() would fail the test


def test_a_missing_planner_is_refused_unless_the_run_is_reflex_only():
    config = HarnessConfig(adapter="mock")
    with pytest.raises(ValueError, match="planner"):
        AgentLoop(env=HOI4Env(MockAdapter(), config), planner=None, config=config)


def test_the_score_line_reports_the_baseline_and_the_delta():
    card = ScoreCard(scenario="economy_ramp", score=0.75, baseline=0.50)
    assert card.render().startswith("economy_ramp: score 0.75 (baseline 0.50, +0.25)")


def test_a_scored_run_reports_itself_against_the_recorded_baseline():
    card = run_scenario("economy_ramp", reflex_only())
    recorded = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["economy_ramp"]

    assert card.baseline == recorded["score"]
    assert f"(baseline {card.baseline:.2f}" in card.render()


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_every_recorded_baseline_is_what_a_reflex_run_scores_today(key):
    card = run_scenario(key, reflex_only())
    assert card.planner_calls == 0
    # The mock and the reflexes are deterministic, so a drifted score means the
    # committed file is stale even though the objective names still match.
    assert card.score == card.baseline


def test_for_scenario_ignores_a_record_whose_objectives_no_longer_match():
    scenario = SCENARIOS["economy_ramp"]
    record = {"score": 0.25, "objectives": {o.name: True for o in scenario.objectives},
              "turns": 40, "harness_version": "0.0.1"}
    assert for_scenario(scenario, {"economy_ramp": record}) == (0.25, None)

    record["objectives"] = {"civ_factories_20": True, "objective_since_removed": False}
    baseline, note = for_scenario(scenario, {"economy_ramp": record})
    assert baseline is None
    assert note and "no longer match" in note

    assert for_scenario(scenario, {}) == (None, None)


def test_a_stale_baseline_is_ignored_and_the_card_says_so(tmp_path, monkeypatch):
    stale = {
        "economy_ramp": {
            "score": 0.9,
            "objectives": {"civ_factories_20": True, "objective_since_removed": True},
            "turns": 40,
            "harness_version": "0.0.1",
        }
    }
    path = tmp_path / "baselines.json"
    path.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr("hoi4_harness.eval.baselines.BASELINE_PATH", path)

    card = run_scenario("economy_ramp", reflex_only())

    assert card.baseline is None
    assert card.baseline_note and "no longer match" in card.baseline_note
    assert "baseline ignored" in card.render()


def test_every_scenario_has_a_recorded_baseline():
    recorded = load()
    assert set(recorded) == set(SCENARIOS)
    for key, scenario in SCENARIOS.items():
        record = recorded[key]
        assert set(record["objectives"]) == {o.name for o in scenario.objectives}
        assert record["turns"] <= scenario.max_turns
        assert record["harness_version"] == __version__
        assert 0.0 <= record["score"] <= 1.0
        assert all(isinstance(passed, bool) for passed in record["objectives"].values())


def test_write_baselines_merges_instead_of_dropping_other_scenarios(tmp_path):
    path = tmp_path / "baselines.json"
    path.write_text(
        json.dumps({"war_readiness": {"score": 0.33, "objectives": {}, "turns": 180,
                                      "harness_version": "0.0.1"}}),
        encoding="utf-8",
    )
    scenario = SCENARIOS["economy_ramp"]
    card = ScoreCard(
        scenario=scenario.key,
        score=0.25,
        objectives={o.name: False for o in scenario.objectives},
        turns=scenario.max_turns,
    )

    write_baselines({"economy_ramp": card}, path)

    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["economy_ramp"]["score"] == 0.25
    assert recorded["economy_ramp"]["harness_version"] == __version__
    assert recorded["war_readiness"]["score"] == 0.33          # untouched


def test_write_baseline_is_refused_while_the_planner_is_enabled(capsys):
    # A baseline recorded from a model run would corrupt what every score is
    # compared against, so the CLI refuses instead of mis-recording.
    assert main(["eval", "--write-baseline", "economy_ramp"]) == 2
    assert "--no-llm" in capsys.readouterr().err


def test_the_cli_scores_every_scenario_with_zero_model_calls(tmp_path, capsys):
    assert main(["eval", "--no-llm", "--run-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "0 model calls" in out
    assert out.count("(baseline ") == len(SCENARIOS)
