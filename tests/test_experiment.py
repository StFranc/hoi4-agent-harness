"""The hybrid experiment (#10): same scenario, same seeds, three arms."""

import pytest

from hoi4_harness.agent.policy import Policy
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval.experiment import ARMS, Experiment, arm_config, run_experiment
from hoi4_harness.eval.metrics import ScoreCard
from hoi4_harness.eval.scenarios import SCENARIOS
from hoi4_harness.eval.variance import AggregateCard
from hoi4_harness.types import DivisionGroup, Front, GameState


def test_each_arm_is_configured_as_the_issue_defines_it():
    base = HarnessConfig(guidance="doctrine")
    model, control = arm_config("model-only", base)
    assert (model.planner_enabled, model.reflex_delegate, control) == (True, False, "llm")
    ai, control = arm_config("AI-only", base)
    assert (ai.planner_enabled, ai.reflex_delegate, control) == (False, True, "ai")
    hybrid, control = arm_config("hybrid", base)
    assert (hybrid.guidance, hybrid.planner_enabled, control) == ("hybrid", True, "ai")
    assert base.guidance == "doctrine"                  # the base config is not mutated


def test_the_delegating_reflex_is_off_by_default_and_hands_over_when_on():
    losing = GameState(divisions=[DivisionGroup("Infantry", 4)],
                       fronts=[Front("east", "SOV", 4, 20, pressure="losing_ground")])
    assert not any(c.name == "delegate_army_to_ai" for c in Policy().reflex_actions(losing))
    names = [c.name for c in Policy(reflex_delegate=True).reflex_actions(losing)]
    assert names[:2] == ["delegate_army_to_ai", "set_ai_posture"]


def test_the_experiment_runs_all_three_arms_at_the_same_seeds(tmp_path):
    result = run_experiment("defensive_war", HarnessConfig(), seeds=2, transcript_dir=tmp_path)
    assert list(result.arms) == list(ARMS)
    assert all(agg.seeds == [1936, 1937] for agg in result.arms.values())
    assert result.arms["AI-only"].cards[0].planner_calls == 0
    assert (tmp_path / "AI-only" / "seed-1936" / "defensive_war.jsonl").exists()
    text = result.render()
    assert "model-only" in text and "AI-only" in text and "hybrid" in text
    assert "verdict:" in text and "measures no model" in text


def _experiment(model, ai, hybrid):
    def agg(score):
        return AggregateCard("x", seeds=[1], cards=[ScoreCard("x", score=score)])
    exp = Experiment(SCENARIOS["defensive_war"], planner="anthropic:m", seeds=5)
    exp.arms = {"model-only": agg(model), "AI-only": agg(ai), "hybrid": agg(hybrid)}
    return exp


@pytest.mark.parametrize(("scores", "phrase"), [
    ((0.5, 0.7, 0.72), "the model is decorative"),
    ((0.8, 0.3, 0.82), "adds nothing over the model alone"),
    ((0.4, 0.3, 0.9), "beats both"),
])
def test_the_verdict_says_no_difference_when_there_is_none(scores, phrase):
    assert phrase in _experiment(*scores).verdict()


def test_the_command_rejects_an_unknown_scenario(capsys):
    assert main(["experiment", "nope"]) == 2
