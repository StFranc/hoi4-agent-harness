"""Measuring brief size and wake rate, peacetime vs wartime (#14)."""

import json

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.cli import main
from hoi4_harness.eval import CAMPAIGNS, measure
from hoi4_harness.eval.campaigns import WARTIME_1939_1941
from hoi4_harness.observation.tokens import count_tokens


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_wartime_campaign_fights_the_war_it_describes():
    c = WARTIME_1939_1941
    adapter = MockAdapter(seed=c.seed, country=c.country, start=c.start, start_state=c.start_state)
    seen_fronts, allies_at_end = set(), None
    while adapter.date.isoformat() < c.until:
        state = adapter.advance(7)
        seen_fronts |= {f.name for f in state.fronts}
        allies_at_end = state.wars
    assert seen_fronts >= {"poland", "westwall", "ardennes", "norway", "balkans",
                           "baltic", "center", "ukraine"}
    final = adapter.read_state()
    assert {f.name for f in final.fronts} == {"baltic", "center", "ukraine"}
    assert {w.against for w in allies_at_end} == {"ENG", "SOV"}
    center = next(f for f in final.fronts if f.name == "center")
    assert center.pressure == "losing_ground" and center.pocket_divisions > 0


def test_a_closed_front_sends_its_divisions_home_in_one_group():
    c = WARTIME_1939_1941
    adapter = MockAdapter(seed=c.seed, country=c.country, start=c.start, start_state=c.start_state)
    while adapter.date.isoformat() < "1940-07-01":
        adapter.advance(7)
    homes = [g for g in adapter.read_state().divisions if g.location == "home"]
    assert len(homes) == 1


def test_the_transcript_counts_every_brief_and_says_how(tmp_path):
    main(["measure", "--campaign", "wartime_1939_1941", "--run-dir", str(tmp_path)])
    observes = [r for r in _records(tmp_path / "wartime_1939_1941.jsonl") if r["kind"] == "observe"]
    assert observes
    for record in observes:
        assert record["brief_tokens"] == count_tokens(record["brief"])[0]
        assert record["token_method"] in {"chars/4", "tiktoken:o200k_base"}
        assert record["prompt_prefix_tokens"] > record["prompt_turn_tokens"] > 0
    assert any(r["at_war"] for r in observes) and not all(r["at_war"] for r in observes)


def test_measure_splits_peacetime_from_wartime(tmp_path, capsys):
    assert main(["measure", "--campaign", "wartime_1939_1941", "--run-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "peacetime" in out and "wartime" in out
    assert "wakes/mo" in out and "brief mean" in out
    assert "this run was scripted" in out

    result = measure(tmp_path / "wartime_1939_1941.jsonl")
    war = result.phases["wartime"]
    assert war.days > 800 and war.wakes > 0 and war.brief_tokens
    assert 0 < war.wakes_per_month < 10
    assert war.reasons["critical event"] >= 1


def test_measure_reads_billed_usage_and_cache_hits_from_a_real_provider(tmp_path):
    path = tmp_path / "t.jsonl"
    rows = [
        {"kind": "observe", "at_war": True, "brief_tokens": 100, "token_method": "chars/4",
         "is_delta": False, "wake_reason": "scheduled review",
         "prompt_prefix_tokens": 1500, "prompt_turn_tokens": 200},
        {"kind": "llm", "model": "claude-x",
         "usage": {"input_tokens": 2000, "cached_input_tokens": 1500, "output_tokens": 90}},
        {"kind": "turn_end", "at_war": True, "woke": True,
         "from_date": "1940-01-01", "date": "1940-01-31"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{torn", encoding="utf-8")
    result = measure(path)
    war = result.phases["wartime"]
    assert war.cache_hit == 0.75 and war.input_tokens == 2000
    assert "75%" in result.render() and "2,000" in result.render()


def test_measure_needs_a_transcript_or_a_campaign(capsys):
    assert main(["measure"]) == 2
    assert main(["measure", "--campaign", "nope"]) == 2
    assert set(CAMPAIGNS) == {"peacetime_1936_1939", "wartime_1939_1941"}
