"""UI scripts (#3): act through the recorded click path, then verify, never assume.

A fake GUI records what would have been sent and a fake reader plays the game,
so nothing here can drive the machine it runs on.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.adapters.ui_scripts import (
    VERIFIERS,
    Step,
    load_scripts,
    parse_steps,
    targets_in,
)
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.cli import calibration_targets
from hoi4_harness.config import HarnessConfig
from hoi4_harness.types import (
    ActionCall,
    ConstructionItem,
    GameState,
    ProductionLine,
    ResearchSlot,
)

FOCUS_SCRIPT = parse_steps([
    {"press": "f"}, {"click": "focus.search"}, {"type": "{focus_id}"},
    {"click": "focus.first_result"}, {"press": "escape"},
])
COORDS = {"focus.search": (10, 10), "focus.first_result": (20, 20)}


class FakeGui:
    def __init__(self, on_click=None):
        self.sent: list[str] = []
        self.on_click = on_click

    def press(self, key):
        self.sent.append(f"press {key}")

    def moveTo(self, x, y, duration=0):
        self.sent.append(f"move {x},{y}")

    def click(self):
        self.sent.append("click")
        if self.on_click:
            self.on_click()

    def write(self, text, interval=0):
        self.sent.append(f"write {text}")


class FakeGame:
    """A reader whose state the fake GUI can change, like the real game would."""

    def __init__(self, **fields):
        self.state = GameState(**fields)

    def read(self) -> GameState:
        return self.state


def _driver(game, gui, dry_run=False, scripts=None, coords=COORDS):
    return InputDriverAdapter(
        InputConfig(coordinates=dict(coords)), dry_run=dry_run,
        focus_check=lambda title: FocusCheck(True, "Hearts of Iron IV"),
        scripts=scripts if scripts is not None else {"set_national_focus": FOCUS_SCRIPT},
        read_state=game.read, sleep=lambda s: None, verify_timeout=1.0, gui=gui,
    )


FOCUS = ActionCall("set_national_focus", {"focus_id": "SWE_expand_industry"})


def test_a_scripted_action_that_worked_is_verified():
    game = FakeGame()
    gui = FakeGui(on_click=lambda: setattr(game.state, "national_focus", "SWE_expand_industry"))
    result = _driver(game, gui).apply(FOCUS)
    assert result.ok and result.message == "done and verified"
    assert "write SWE_expand_industry" in gui.sent


def test_a_click_that_changed_nothing_is_a_failure_not_a_success():
    game = FakeGame()
    result = _driver(game, FakeGui()).apply(FOCUS)
    assert not result.ok and result.error_kind == "rejected"
    assert "the running focus is none, not SWE_expand_industry" in result.message


def test_a_field_the_reader_cannot_see_is_reported_unverified():
    game = FakeGame(unknown_fields=["national_focus"])
    result = _driver(game, FakeGui()).apply(FOCUS)
    assert not result.ok and result.error_kind == "unverified"
    assert "cannot see national_focus" in result.message


def test_dry_run_logs_the_path_and_sends_nothing():
    gui = FakeGui()
    driver = _driver(FakeGame(), gui, dry_run=True)
    result = driver.apply(FOCUS)
    assert result.error_kind == "not_executed" and "would press f; click focus.search" in result.message
    assert gui.sent == []
    assert "type SWE_expand_industry" in driver.log


def test_a_missing_calibration_refuses_before_sending_anything():
    gui = FakeGui()
    result = _driver(FakeGame(), gui, coords={"focus.search": (1, 1)}).apply(FOCUS)
    assert not result.ok and "focus.first_result" in result.message
    assert gui.sent == []


def test_without_a_reader_nothing_is_sent():
    gui = FakeGui()
    driver = _driver(FakeGame(), gui)
    driver._read_state = None
    assert driver.apply(FOCUS).error_kind == "unverified" and gui.sent == []


def test_an_action_with_no_recorded_script_is_refused():
    driver = _driver(FakeGame(), FakeGui())
    result = driver.apply(ActionCall("start_research", {"technology": "x"}))
    assert result.error_kind == "unsupported"
    assert "start_research" not in driver.supported_actions
    assert "set_national_focus" in driver.supported_actions


def test_a_step_with_a_missing_argument_is_a_rejection_not_a_crash():
    script = parse_steps([{"type": "{nope}"}])
    driver = _driver(FakeGame(), FakeGui(), scripts={"set_national_focus": script})
    result = driver.apply(FOCUS)
    assert not result.ok and "needs argument(s) ['nope']" in result.message


@pytest.mark.parametrize(("name", "args", "before", "after"), [
    ("start_research", {"technology": "t"},
     GameState(research=[ResearchSlot(0)]), GameState(research=[ResearchSlot(0, "t", 50)])),
    ("queue_construction", {"building": "dockyard", "state": "1", "count": 1},
     GameState(), GameState(construction=[ConstructionItem("dockyard", "1")])),
    ("set_production", {"equipment": "e", "factories": 3},
     GameState(), GameState(production=[ProductionLine("e", 3)])),
    ("hire_advisor", {"advisor_id": "a"}, GameState(political_power=200), GameState(political_power=50)),
])
def test_each_peacetime_verifier_accepts_the_change_and_rejects_its_absence(name, args, before, after):
    assert VERIFIERS[name](before, after, args) is None
    assert VERIFIERS[name](before, before, args) is not None


def test_the_composite_gives_the_writer_its_reader():
    game_reader = MockAdapter()
    writer = InputDriverAdapter(dry_run=True, scripts={"set_national_focus": FOCUS_SCRIPT})
    composite = CompositeAdapter(game_reader, writer)
    assert writer._read_state == game_reader.read_state
    assert "set_national_focus" in composite.supported_actions


def test_a_scripts_file_is_loaded_and_its_targets_offered_to_calibrate(tmp_path, capsys):
    path = tmp_path / "ui_scripts.json"
    path.write_text(json.dumps({"schema": 1, "scripts": {
        "set_national_focus": [{"press": "f"}, {"click": "focus.search"}],
        "start_research": [{"click": "research.tech.{technology}"}],
    }}))
    scripts = load_scripts(path)
    assert scripts["set_national_focus"][1] == Step("click", "focus.search")
    assert targets_in(scripts) == ["focus.search", "research.tech.{technology}"]
    assert calibration_targets(HarnessConfig(run_dir=tmp_path)) == ["focus.search"]
    assert "research.tech.{technology}" in capsys.readouterr().err


def test_a_script_for_an_action_with_no_verification_is_refused(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schema": 1, "scripts": {"diplomacy": [{"press": "j"}]}}))
    with pytest.raises(ValueError, match="no verification exists for 'diplomacy'"):
        load_scripts(path)


def test_a_bad_step_or_schema_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown step 'drag'"):
        parse_steps([{"drag": "x"}])
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schema": 9, "scripts": {}}))
    with pytest.raises(ValueError, match="schema 9"):
        load_scripts(path)


def test_the_shipped_template_parses():
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / "profiles" / "ui_scripts.example.json"
    assert set(load_scripts(template)) == set(VERIFIERS)


def test_build_adapter_loads_scripts_from_the_run_dir(tmp_path):
    from hoi4_harness.adapters import build_adapter

    (tmp_path / "ui_scripts.json").write_text(json.dumps(
        {"schema": 1, "scripts": {"set_national_focus": [{"press": "f"}]}}))
    log = tmp_path / "game.log"
    log.write_text("")
    adapter = build_adapter(HarnessConfig(adapter="logtail+input", run_dir=tmp_path, log_path=log))
    assert "set_national_focus" in adapter.supported_actions


# --- the hybrid actions (#8) ---------------------------------------------------


@pytest.mark.parametrize(("name", "args", "before", "after"), [
    ("delegate_army_to_ai", {"army": "1st Army", "delegate": True},
     GameState(), GameState(delegated_armies=["1st Army"])),
    ("delegate_army_to_ai", {"army": "all", "delegate": False},
     GameState(delegated_armies=["all"]), GameState()),
    ("set_ai_posture", {"posture": "defensive"}, GameState(), GameState(posture="defensive")),
    ("set_ai_posture", {"posture": "offensive", "theater": "east"},
     GameState(), GameState(theater_postures={"east": "offensive"})),
    ("set_ai_directive", {"directive": "protect", "target": "FIN"},
     GameState(), GameState(ai_directives=["protect FIN (100)"])),
    ("clear_ai_directives", {},
     GameState(ai_directives=["protect FIN (100)"], posture="defensive"), GameState()),
])
def test_each_hybrid_verifier_accepts_the_change_and_rejects_its_absence(name, args, before, after):
    assert VERIFIERS[name](before, after, args) is None
    assert VERIFIERS[name](before, before, args) is not None


def test_a_directive_for_the_wrong_country_does_not_verify():
    after = GameState(ai_directives=["protect FINX (100)"])
    assert VERIFIERS["set_ai_directive"](GameState(), after,
                                         {"directive": "protect", "target": "FIN"})


def test_delegation_through_the_log_reader_is_unverified_not_assumed(tmp_path):
    from hoi4_harness.adapters.logtail import LogTailAdapter

    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n")
    reader = LogTailAdapter(log)
    driver = InputDriverAdapter(
        InputConfig(coordinates={"army.all": (1, 1), "army.ai_control_toggle": (2, 2)}),
        dry_run=False, focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"),
        scripts={"delegate_army_to_ai": parse_steps(
            [{"click": "army.{army}"}, {"click": "army.ai_control_toggle"}])},
        read_state=reader.read_state, sleep=lambda s: None, gui=FakeGui(),
    )
    result = driver.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    assert result.error_kind == "unverified" and "delegated_armies" in result.message


def test_the_log_reader_learns_posture_from_the_mods_events(tmp_path):
    from hoi4_harness.adapters.logtail import LogTailAdapter

    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n")
    reader = LogTailAdapter(log)
    assert not reader.read_state().known("posture")
    with log.open("a") as handle:
        handle.write("[x]: LLMB|v1|evt|tag=SWE|kind=10\n")
    state = reader.read_state()
    assert state.known("posture") and state.posture == "defensive"
    with log.open("a") as handle:
        handle.write("[x]: LLMB|v1|evt|tag=SWE|kind=12\n")
    assert reader.read_state().posture is None


def test_verification_polling_does_not_drain_the_events_the_wake_rule_needs(tmp_path):
    from hoi4_harness.adapters.logtail import LogTailAdapter

    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n"
                   "[x]: LLMB|v1|evt|tag=SWE|kind=1|detail=war\n")
    reader = LogTailAdapter(log)
    writer = InputDriverAdapter(dry_run=True)
    CompositeAdapter(reader, writer)
    writer._read_state()                       # what verification polling does
    writer._read_state()
    assert [e.kind for e in reader.read_state().events] == ["war_declared"]


def test_an_unobservable_focus_is_not_shouted_as_none_selected():
    from hoi4_harness.observation import render_full

    assert "Focus: unknown" in render_full(GameState(unknown_fields=["national_focus"]))
    assert "NONE SELECTED" in render_full(GameState())


def test_a_theater_posture_through_the_log_reader_is_unverified(tmp_path):
    from hoi4_harness.adapters.logtail import LogTailAdapter

    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n"
                   "[x]: LLMB|v1|evt|tag=SWE|kind=10\n")
    driver = InputDriverAdapter(
        InputConfig(coordinates={"p": (1, 1)}), dry_run=False,
        focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"),
        scripts={"set_ai_posture": parse_steps([{"click": "p"}])},
        read_state=LogTailAdapter(log).peek_state, sleep=lambda s: None, gui=FakeGui(),
    )
    result = driver.apply(ActionCall("set_ai_posture", {"posture": "offensive", "theater": "east"}))
    assert result.error_kind == "unverified" and "theater_postures" in result.message


def test_the_save_reader_does_not_claim_fields_it_never_reads(tmp_path):
    from hoi4_harness.adapters.savegame import SaveGameAdapter

    (tmp_path / "a.hoi4").write_text('date="1936.1.1.12"\nplayer="SWE"\n')
    state = SaveGameAdapter(tmp_path).read_state()
    for name in ("national_focus", "posture", "ai_directives", "delegated_armies"):
        assert not state.known(name)
