"""Directive feedback (#11): the brief says what a directive changed, not that it was recorded."""

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.observation.directives import DirectiveTracker
from hoi4_harness.types import ActionCall, DivisionGroup, Front, GameState, ScenarioStart, War


def _env() -> HOI4Env:
    adapter = MockAdapter(
        country="GER",
        start="1939-09-01",
        start_state=ScenarioStart(
            wars=[War(against="POL")],
            fronts=[Front(name="east", enemy="POL", divisions_enemy=10)],
            divisions=[
                DivisionGroup(template="Infantry", count=4, location="east"),
                DivisionGroup(template="Infantry", count=12, location="home"),
            ],
        ),
    )
    config = HarnessConfig(operational_control="ai", dry_run=False, require_confirmation=False)
    env = HOI4Env(adapter, config)
    env.reset()
    return env


def _directive(env, directive, target):
    return env.act(ActionCall("set_ai_directive", {"directive": directive, "target": target}))


def test_a_directive_the_ai_acted_on_shows_its_consequence():
    env = _env()
    env.act(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    assert _directive(env, "invade", "POL").ok
    env.advance(14)
    brief = env.observe().brief
    assert "Directive effect (observed, not intended):" in brief
    assert "invade POL, raised 14d ago: divisions facing POL 4 -> 16 on 1 front(s)" in brief


def test_a_directive_nothing_happened_to_says_so():
    env = _env()
    assert _directive(env, "invade", "DEN").ok
    env.advance(21)
    assert "invade DEN, raised 21d ago: NO OBSERVABLE CHANGE" in env.observe().brief


def test_the_status_survives_delta_briefs():
    env = _env()
    _directive(env, "protect", "FIN")
    env.observe()                      # the full brief
    env.advance(7)
    observation = env.observe()
    assert observation.is_delta
    assert "protect FIN" in observation.brief


def test_reweighting_keeps_the_original_baseline_and_date():
    env = _env()
    _directive(env, "invade", "DEN")
    env.advance(21)
    env.act(ActionCall("set_ai_directive",
                       {"directive": "invade", "target": "DEN", "weight": 400}))
    assert "raised 21d ago" in env.observe().brief


def test_clearing_directives_clears_the_status():
    env = _env()
    _directive(env, "invade", "DEN")
    env.act(ActionCall("clear_ai_directives", {}))
    assert "Directive effect" not in env.observe().brief


def test_a_rejected_directive_is_not_tracked():
    env = _env()
    env.config.dry_run = True
    env.config.require_confirmation = True
    assert not _directive(env, "invade", "DEN").ok
    assert env.directives.standing == {}


def test_war_and_alliance_changes_are_reported():
    tracker = DirectiveTracker()
    tracker.raised("befriend", "ITA", 100, GameState(date="1939-01-01"))
    later = GameState(date="1939-02-01", wars=[War(against="FRA", allies=["ITA"])])
    assert tracker.render(later)[1].strip() == "befriend ITA, raised 31d ago: now fighting on our side"


def test_the_status_block_is_capped():
    tracker = DirectiveTracker(max_lines=2)
    for tag in ("DEN", "NOR", "SWE"):
        tracker.raised("contain", tag, 100, GameState())
    lines = tracker.render(GameState())
    assert len(lines) == 4 and lines[-1] == "  + 1 more"
