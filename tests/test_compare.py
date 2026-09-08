"""The compare command: one scenario, several models, one table.

The two rules the table exists to enforce are pinned here -- spend printed beside
score, and the fixed configuration printed above it -- along with the promise
that one broken model costs its row, never the rest of the comparison.
"""

from __future__ import annotations

from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.cli import main


def compare_args(tmp_path, *extra):
    return ["compare", "economy_ramp", "--run-dir", str(tmp_path), *extra]


def rows_for(out: str, model: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(model)]


def test_two_models_get_a_row_each_above_the_baseline_row(tmp_path, capsys):
    assert main(compare_args(tmp_path, "--models", "scripted:scripted,scripted:other")) == 0
    out = capsys.readouterr().out

    assert rows_for(out, "scripted:scripted")
    assert rows_for(out, "scripted:other")
    assert rows_for(out, "baseline (reflex)")
    # The baseline is the thing every row is judged against, so it comes first.
    assert out.index("baseline (reflex)") < out.index("scripted:scripted") < out.index(
        "scripted:other"
    )


def test_the_fixed_configuration_is_printed_and_names_the_guidance_pack_and_seed(tmp_path, capsys):
    assert main(compare_args(tmp_path, "--days", "3")) == 0
    out = capsys.readouterr().out

    assert "guidance=doctrine" in out
    assert "seed=1936" in out
    assert "control=llm" in out
    assert "days_per_turn=3" in out


def test_a_model_that_raises_is_reported_as_failed_and_the_others_still_run(tmp_path, capsys):
    # An unknown provider fails at client construction, exactly where a missing
    # key or a missing SDK fails for the real ones.
    assert main(compare_args(tmp_path, "--models", "bogus:doomed,scripted:scripted")) == 0
    out = capsys.readouterr().out

    failed, good = rows_for(out, "bogus:doomed")[0], rows_for(out, "scripted:scripted")[0]
    assert "--" in failed                      # nothing to report: no score, no spend
    assert "failed: ValueError" in out and "Unknown provider 'bogus'" in out
    assert "--" not in good                    # the healthy model still scored a full row
    assert "failed" not in good


def test_the_table_reports_tokens_and_dollars_alongside_the_score(tmp_path, capsys):
    # No --models: the configured planner is compared against the baseline.
    assert main(compare_args(tmp_path)) == 0
    out = capsys.readouterr().out

    for column in ("score", "delta", "calls", "tokens in", "tokens out", "usd"):
        assert column in out
    row = rows_for(out, "scripted:scripted")[0]
    assert "--" not in row                     # a working model reports spend, even at $0.00
    assert "$0.00" in out                      # the reflex baseline plays for free


def test_a_run_the_provider_killed_partway_keeps_its_score_but_says_so(tmp_path, monkeypatch, capsys):
    real_complete = ScriptedClient.complete

    def complete(self, system, messages, tools=None, max_tokens=None):
        # "unauthorized" classifies as fatal, so the loop stops the run rather
        # than grinding through every remaining turn on reflexes.
        if self.model == "doomed":
            raise RuntimeError("401 unauthorized")
        return real_complete(self, system, messages, tools, max_tokens)

    monkeypatch.setattr(ScriptedClient, "complete", complete)
    assert main(compare_args(tmp_path, "--models", "scripted:doomed")) == 0
    out = capsys.readouterr().out

    assert "stopped early: fatal provider error" in out
    assert "--" not in rows_for(out, "scripted:doomed")[0]
