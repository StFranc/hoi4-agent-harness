"""Per-target directive generation (#7): a directive raises the block for the country named."""

import re

import pytest

from hoi4_harness import modgen
from hoi4_harness.actions.catalog import AI_DIRECTIVES
from hoi4_harness.adapters.logtail import EVENT_CODES
from hoi4_harness.cli import main


def _files(tags=("FIN", "POL")):
    return {item.path: item.text for item in modgen.generate(tags)}


def test_protect_fin_and_protect_pol_are_different_blocks_with_the_right_id():
    strategies = _files()["common/ai_strategy/llm_bridge_targets.txt"]
    fin = re.search(r"llmb_protect_FIN = \{(.*?)\n\}", strategies, flags=re.S).group(1)
    pol = re.search(r"llmb_protect_POL = \{(.*?)\n\}", strategies, flags=re.S).group(1)
    assert 'id = "FIN"' in fin and "POL" not in fin
    assert 'id = "POL"' in pol and "FIN" not in pol
    assert "has_country_flag = llmb_protect_FIN" in fin


def test_every_directive_has_a_block_per_target():
    strategies = _files()["common/ai_strategy/llm_bridge_targets.txt"]
    for directive in AI_DIRECTIVES:
        for tag in ("FIN", "POL"):
            assert f"llmb_{directive}_{tag} = {{" in strategies


def test_raising_a_directive_sets_the_flag_for_the_selected_target_only():
    effects = _files()["common/scripted_effects/llm_bridge_targets.txt"]
    raise_protect = re.search(r"llm_bridge_raise_protect = \{(.*?)\n\}", effects, flags=re.S).group(1)
    assert ("if = { limit = { has_country_flag = llmb_target_POL } "
            "set_country_flag = llmb_protect_POL ") in raise_protect
    # ...and says so in a literal line the log reader can rebuild directives from.
    assert '|kind=14|detail=protect POL" }' in raise_protect
    assert "llm_bridge_emit_event" not in raise_protect
    select_pol = re.search(r"llm_bridge_select_POL = \{(.*?)\n\}", effects, flags=re.S).group(1)
    # Selecting a target clears the previous one, so two clicks cannot raise
    # a directive against the wrong country.
    assert "llm_bridge_clear_target = yes" in select_pol
    assert "set_country_flag = llmb_target_POL" in select_pol


def test_decisions_are_targets_plus_directives_not_their_product():
    decisions = _files()["common/decisions/llm_bridge_target_decisions.txt"]
    names = re.findall(r"^\t(llmb_\w+) = \{", decisions, flags=re.M)
    assert len(names) == 2 + len(AI_DIRECTIVES)


def test_the_emitted_event_codes_are_ones_the_harness_reads():
    assert EVENT_CODES[str(modgen.EVENT_TARGET_SELECTED)][0] == "directive_target_selected"
    assert EVENT_CODES[str(modgen.EVENT_DIRECTIVE_RAISED)][0] == "directive_raised"


def test_generation_is_deterministic_and_deduplicates_tags():
    assert modgen.generate(["fin", "POL", "FIN"]) == modgen.generate(["FIN", "POL"])


def test_a_bad_tag_is_refused():
    with pytest.raises(ValueError, match="not a country tag"):
        modgen.generate(["FINLAND"])


def test_the_cli_regenerates_for_a_playset_and_checks_drift(tmp_path, capsys):
    assert main(["mod-directives", "--mod-dir", str(tmp_path), "--tags", "GER,FIN"]) == 0
    assert main(["mod-directives", "--mod-dir", str(tmp_path), "--tags", "GER,FIN",
                 "--check"]) == 0
    assert main(["mod-directives", "--mod-dir", str(tmp_path), "--tags", "GER,POL",
                 "--check"]) == 1
    assert "stale:" in capsys.readouterr().err
    loc = tmp_path / "localisation" / "english" / "llm_bridge_targets_l_english.yml"
    assert loc.read_bytes().startswith(b"\xef\xbb\xbf")


def test_the_log_reader_rebuilds_standing_directives_from_the_generated_lines(tmp_path):
    from hoi4_harness.adapters.logtail import LogTailAdapter

    raise_protect = re.search(r"llm_bridge_raise_protect = \{(.*?)\n\}",
                              _files()["common/scripted_effects/llm_bridge_targets.txt"],
                              flags=re.S).group(1)
    line = re.search(r'log = "(.*?)"', raise_protect).group(1).replace("[ROOT.GetTag]", "SWE")
    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n")
    reader = LogTailAdapter(log)
    assert not reader.read_state().known("ai_directives")
    with log.open("a") as handle:
        handle.write(f"[x]: {line}\n")
    state = reader.read_state()
    assert state.known("ai_directives") and state.ai_directives == ["protect FIN"]
    with log.open("a") as handle:
        handle.write("[x]: LLMB|v1|evt|tag=SWE|kind=12\n")
    assert reader.read_state().ai_directives == []
