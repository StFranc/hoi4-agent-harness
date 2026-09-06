import pytest

from hoi4_harness.adapters.logtail import SCHEMA_VERSION, LogTailAdapter, parse_date, parse_line

STATE = (
    "[messagehandler.cpp:290]: LLMB|v1|state|date=1936.2.14|tag=SWE|pp=42|stab=63|ws=18"
    "|civ=12|mil=5|doc=4|mp=214000\n"
)
EVENT = "[messagehandler.cpp:290]: LLMB|v1|evt|tag=SWE|kind=1|detail=Germany declared war\n"
NOISE = "[graphics.cpp:12]: some unrelated engine chatter\n"


def write_log(tmp_path, *lines):
    path = tmp_path / "game.log"
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)
    return path


def test_parses_a_telemetry_line_out_of_engine_noise():
    kind, fields = parse_line(STATE)
    assert kind == "state"
    assert fields["tag"] == "SWE" and fields["pp"] == "42"
    assert parse_line(NOISE) is None


def test_a_schema_mismatch_fails_loudly():
    with pytest.raises(ValueError, match="do not run mismatched"):
        parse_line(STATE.replace("|v1|", f"|v{SCHEMA_VERSION + 7}|"))


@pytest.mark.parametrize(
    "raw,expected",
    [("1936.2.14", "1936-02-14"), ("1936-02-14", "1936-02-14"), ("14 February 1936", "1936-02-14")],
)
def test_date_shapes_the_game_can_emit(raw, expected):
    assert parse_date(raw) == expected


def test_an_unparseable_date_is_none_not_a_guess():
    assert parse_date("sometime in the spring") is None


def test_state_is_rebuilt_from_the_log(tmp_path):
    adapter = LogTailAdapter(write_log(tmp_path, NOISE, STATE))
    state = adapter.read_state()
    assert state.country == "SWE"
    assert state.date == "1936-02-14"
    assert state.political_power == 42
    assert state.stability == pytest.approx(0.63)
    assert state.civilian_factories == 12


def test_fields_the_mod_does_not_emit_stay_unknown(tmp_path):
    adapter = LogTailAdapter(write_log(tmp_path, STATE))
    state = adapter.read_state()
    assert "production" in state.unknown_fields
    assert "political_power" not in state.unknown_fields   # this one did arrive


def test_an_unexpanded_token_is_treated_as_missing_not_zero(tmp_path):
    broken = STATE.replace("pp=42", "pp=[?llmb_pp]")
    adapter = LogTailAdapter(write_log(tmp_path, broken))
    state = adapter.read_state()
    assert "political_power" in state.unknown_fields
    assert state.political_power == 0.0 and not state.known("political_power")


def test_only_new_lines_are_consumed_on_each_poll(tmp_path):
    path = write_log(tmp_path, STATE)
    adapter = LogTailAdapter(path)
    assert adapter.poll() == 1
    assert adapter.poll() == 0
    write_log(tmp_path, EVENT)
    assert adapter.poll() == 1


def test_a_war_declaration_arrives_as_a_critical_event(tmp_path):
    adapter = LogTailAdapter(write_log(tmp_path, STATE, EVENT))
    state = adapter.read_state()
    assert [(e.kind, e.severity) for e in state.events] == [("war_declared", "critical")]
    assert adapter.read_state().events == []      # events are drained, not repeated


def test_a_log_with_no_telemetry_says_so(tmp_path):
    adapter = LogTailAdapter(write_log(tmp_path, NOISE))
    with pytest.raises(RuntimeError, match="LLM Bridge"):
        adapter.read_state()


def test_it_refuses_to_act(tmp_path):
    from hoi4_harness.types import ActionCall

    adapter = LogTailAdapter(write_log(tmp_path, STATE))
    result = adapter.apply(ActionCall("set_production", {"equipment": "x", "factories": 1}))
    assert not result.ok and result.error_kind == "unsupported"
