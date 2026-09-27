"""Variance (#18): N seeds, reported as a median with its range."""

from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval import SCENARIOS, AggregateCard, compare, run_seeds
from hoi4_harness.eval.metrics import ScoreCard


def _card(score, **kwargs):
    return ScoreCard(scenario="economy_ramp", score=score,
                     objectives={"a": score >= 0.5, "b": score >= 1.0}, **kwargs)


def test_an_aggregate_reports_n_median_min_and_max():
    agg = AggregateCard("economy_ramp", seeds=[1, 2, 3, 4, 5],
                        cards=[_card(s) for s in (0.25, 0.5, 0.5, 0.75, 1.0)])
    assert (agg.n, agg.median, agg.low, agg.high) == (5, 0.5, 0.25, 1.0)
    text = agg.render()
    assert "median 0.50 [0.25-1.00] over 5 seeds" in text
    assert "[4/5] a" in text and "[1/5] b" in text
    assert "anecdote" not in text


def test_too_few_seeds_is_labelled_an_anecdote():
    agg = AggregateCard("economy_ramp", seeds=[1, 2], cards=[_card(0.5), _card(0.5)])
    assert "an anecdote, not a result" in agg.render()


def test_the_baseline_delta_is_taken_from_the_median():
    agg = AggregateCard("economy_ramp", seeds=[1, 2, 3],
                        cards=[_card(s, baseline=0.25) for s in (0.25, 0.75, 1.0)])
    assert "(baseline 0.25, +0.50)" in agg.render()


def test_runs_that_stopped_short_are_counted():
    cards = [_card(0.5, reached_end_date=False), _card(0.5, reached_end_date=True)]
    agg = AggregateCard("economy_ramp", seeds=[1, 2], cards=cards)
    assert "1/2 runs STOPPED SHORT" in agg.render()


def test_each_seed_is_a_separate_run_with_its_own_transcript(tmp_path):
    config = HarnessConfig(planner_enabled=False)
    agg = run_seeds(SCENARIOS["economy_ramp"], config, 3, transcript_dir=tmp_path)
    assert agg.seeds == [1936, 1937, 1938] and agg.n == 3
    for seed in agg.seeds:
        assert (tmp_path / f"seed-{seed}" / "economy_ramp.jsonl").exists()


def test_eval_seeds_prints_the_spread(tmp_path, capsys):
    assert main(["eval", "economy_ramp", "--no-llm", "--seeds", "2",
                 "--run-dir", str(tmp_path)]) == 0
    assert "over 2 seeds" in capsys.readouterr().out


def test_a_baseline_is_not_recorded_from_a_multi_seed_run(tmp_path, capsys):
    assert main(["eval", "--no-llm", "--seeds", "3", "--write-baseline",
                 "--run-dir", str(tmp_path)]) == 2
    assert "drop --seeds" in capsys.readouterr().err


def test_compare_with_seeds_prints_a_range_column(tmp_path):
    config = HarnessConfig(run_dir=tmp_path)
    table = compare("economy_ramp", ["scripted:scripted"], config, seeds=2).render()
    assert "median" in table and "range" in table
    assert "seeds=1936..1937 (n=2)" in table
    assert "an anecdote" in table
