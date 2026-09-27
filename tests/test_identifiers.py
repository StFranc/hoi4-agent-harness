"""The identifier index (#53): what exists in this playset, and refusing what does not."""

import json
from pathlib import Path

import pytest

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.prompts import build_system
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval.runner import PlaysetMismatch, run_scenario
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import ActionCall, GameState

FIXTURE = Path(__file__).parent / "fixtures" / "playset"
GAME = FIXTURE / "game"
TOTAL_CONVERSION = FIXTURE / "mods" / "new_order"
BRIDGE = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


@pytest.fixture(scope="module")
def vanilla() -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(GAME))


@pytest.fixture(scope="module")
def conversion() -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(GAME, [TOTAL_CONVERSION]))


def _check(index, name, country="SWE", **arguments):
    return index.check(ActionCall(name, arguments), country)


# --- reading the playset ------------------------------------------------------


def test_vanilla_lists_its_countries_and_trees(vanilla):
    assert vanilla.playset.name == "vanilla"
    assert vanilla.tags == {"SWE", "GER", "FIN", "POL"}
    assert {t.id for t in vanilla.trees} == {"swedish_focus", "generic_focus"}
    assert vanilla.technologies == {"infantry_weapons", "infantry_weapons1"}
    assert vanilla.states["1"].name == "Stockholm" and vanilla.states["1"].owner == "SWE"


def test_a_country_gets_its_own_tree_plus_the_shared_focuses_it_pulls_in(vanilla):
    focuses = set(vanilla.focuses_for("SWE"))
    assert {"SWE_expand_industry", "SWE_modern_army", "SHARED_root", "SHARED_child"} <= focuses
    assert "SHARED_unrelated" not in focuses
    assert "GEN_army_effort" not in focuses


def test_a_country_without_a_tree_gets_the_default_one(vanilla):
    assert set(vanilla.focuses_for("FIN")) == {"GEN_army_effort", "GEN_equipment_effort"}


def test_prerequisites_read_or_within_a_group_and_and_across_groups(vanilla):
    assert set(vanilla.available_focuses("SWE", [])) == {"SWE_expand_industry", "SHARED_root"}
    after_one = set(vanilla.available_focuses("SWE", ["SWE_expand_industry"]))
    assert {"SWE_civilian_industry", "SWE_arms_industry"} <= after_one      # OR met
    assert "SWE_modern_army" not in after_one                               # AND not met
    both = ["SWE_expand_industry", "SWE_civilian_industry", "SWE_arms_industry"]
    assert "SWE_modern_army" in vanilla.available_focuses("SWE", both)


def test_a_total_conversion_replaces_what_it_says_it_replaces(conversion):
    assert conversion.playset.name == "New Order Fixture"
    assert conversion.tags == {"SWE", "GER", "FIN", "POL", "NFA"}      # tags were added to
    assert {t.id for t in conversion.trees} == {"nfa_focus"}            # focus trees replaced
    assert conversion.states["1"].owner == "NFA"                         # states replaced
    assert conversion.states["1"].name == "Algiers"
    assert conversion.technologies == {"infantry_weapons", "infantry_weapons1"}  # untouched


def test_the_bridge_mod_does_not_make_a_playset_non_vanilla():
    playset = Playset.from_paths(GAME, [BRIDGE])
    assert playset.name == "vanilla"
    assert Playset.from_paths(GAME, [BRIDGE, TOTAL_CONVERSION]).name == "New Order Fixture"


def test_a_folder_that_is_not_an_install_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not look like a HOI4 install"):
        Playset.from_paths(tmp_path)


# --- refusing what does not exist --------------------------------------------


def test_an_invented_focus_is_rejected_with_near_matches(vanilla):
    result = _check(vanilla, "set_national_focus", focus_id="SWE_expand_industries")
    assert not result.ok and result.error_kind == "invalid_args"
    assert "There is no focus 'SWE_expand_industries'" in result.message
    assert "Nearest real ones: SWE_expand_industry" in result.message


def test_a_real_focus_from_someone_elses_tree_says_so(vanilla):
    result = _check(vanilla, "set_national_focus", country="FIN", focus_id="SWE_arms_industry")
    assert "'SWE_arms_industry' is not in FIN's focus tree" in result.message


def test_an_invented_focus_is_rejected_in_a_mod_too(conversion):
    result = _check(conversion, "set_national_focus", country="NFA", focus_id="NFA_return_to_pari")
    assert "Nearest real ones: NFA_return_to_paris" in result.message
    gone = _check(conversion, "set_national_focus", focus_id="SWE_expand_industry")
    assert "There is no focus 'SWE_expand_industry' in this playset (New Order Fixture)" in gone.message
    assert _check(conversion, "set_national_focus", country="NFA",
                  focus_id="NFA_return_to_paris") is None


def test_real_ids_pass(vanilla):
    assert _check(vanilla, "set_national_focus", focus_id="SWE_expand_industry") is None
    assert _check(vanilla, "start_research", technology="infantry_weapons1") is None
    assert _check(vanilla, "queue_construction", building="dockyard", state="Stockholm") is None
    assert _check(vanilla, "queue_construction", building="dockyard", state="1") is None
    assert _check(vanilla, "queue_construction", building="dockyard", state="capital") is None
    assert _check(vanilla, "set_ai_directive", directive="protect", target="FIN") is None


def test_invented_technologies_states_and_tags_are_rejected(vanilla):
    assert "infantry_weapons" in _check(vanilla, "start_research", technology="infantry_weapon").message
    assert "no state 'Stokholm'" in _check(vanilla, "queue_construction", building="dockyard",
                                           state="Stokholm").message
    assert "no country 'FNL'" in _check(vanilla, "set_ai_directive", directive="protect",
                                        target="FNL").message
    assert "no country" in _check(vanilla, "set_trade", resource="oil", from_country="XXX",
                                  factories=1).message


# --- in the loop --------------------------------------------------------------


def test_the_env_rejects_before_the_adapter_sees_it_and_the_brief_offers_the_slice(vanilla):
    env = HOI4Env(MockAdapter(), HarnessConfig(), index=vanilla)
    result = env.act(ActionCall("set_national_focus", {"focus_id": "SWE_industrialization"}))
    assert not result.ok and "Nearest real ones" in result.message
    assert env.read_state().national_focus is None           # never reached the mock
    brief = env.observe().brief
    assert "Focuses available now: SWE_expand_industry, SHARED_root" in brief


def test_the_slice_disappears_while_a_focus_runs_and_follows_completions(vanilla):
    env = HOI4Env(MockAdapter(), HarnessConfig(), index=vanilla)
    assert env.act(ActionCall("set_national_focus", {"focus_id": "SWE_expand_industry"})).ok
    assert "Focuses available" not in env.observe().brief
    env.adapter.advance(120)
    brief = env.observe().brief
    assert "SWE_civilian_industry" in brief and "SWE_expand_industry," not in brief


def test_without_observable_completions_the_slice_says_it_shows_roots(vanilla):
    state = GameState(country="SWE", unknown_fields=["completed_focuses"])
    assert vanilla.brief_lines(state) == [
        "Focus tree roots (completed focuses not observable): SWE_expand_industry, SHARED_root"
    ]


def test_the_transcript_records_the_playset(tmp_path, conversion):
    config = HarnessConfig(planner_enabled=False)
    env = HOI4Env(MockAdapter(), config, index=conversion)
    AgentLoop(env, planner=None, config=config, transcript_path=tmp_path / "t.jsonl").run(1)
    first = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    assert first["kind"] == "playset" and first["name"] == "New Order Fixture"
    assert first["fingerprint"] == conversion.fingerprint()


def test_a_non_vanilla_playset_tells_the_model_not_to_trust_its_memory():
    assert "playset 'New Order Fixture'" in build_system(playset="New Order Fixture")
    assert "playset" not in build_system(playset="vanilla")


def test_a_scenario_refuses_a_playset_it_was_not_written_for(tmp_path):
    config = HarnessConfig(planner_enabled=False, game_dir=GAME, mod_dirs=[TOTAL_CONVERSION])
    with pytest.raises(PlaysetMismatch, match="written for the playset 'vanilla'"):
        run_scenario("economy_ramp", config)
    vanilla_config = HarnessConfig(planner_enabled=False, game_dir=GAME, run_dir=tmp_path)
    assert run_scenario("economy_ramp", vanilla_config).turns > 0


def test_the_index_command_reports_countries_and_a_countrys_focuses(capsys):
    assert main(["index", "--game-dir", str(GAME), "--mod", str(TOTAL_CONVERSION),
                 "--country", "NFA"]) == 0
    out = capsys.readouterr().out
    assert "New Order Fixture" in out and "NFA" in out
    assert "NFA: nfa_focus -- 1 focuses" in out
    assert "start with: NFA_return_to_paris" in out


def test_the_index_command_needs_an_install(capsys, monkeypatch):
    monkeypatch.delenv("HOI4_GAME_DIR", raising=False)
    assert main(["index"]) == 2
