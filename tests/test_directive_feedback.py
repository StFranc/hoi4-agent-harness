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


# --- the log-tail reader (#69): fronts and wars are unknown there -----------

LLMB = "[messagehandler.cpp:290]: LLMB|v{v}|"


def _llmb(body: str) -> str:
    from hoi4_harness.adapters.logtail import SCHEMA_VERSION

    return LLMB.format(v=SCHEMA_VERSION) + body + "\n"


def _logtail_env(tmp_path):
    """HOI4Env on the real log-tail reader, with a directive that lands in game.log.

    Stands in for the input driver: the click is "the mod logs its kind=14
    directive_raised line", which is all the live path has to show for it.
    """
    from hoi4_harness.adapters.logtail import LogTailAdapter
    from hoi4_harness.types import ActionResult

    log = tmp_path / "game.log"

    def append(line: str) -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(line)

    class DirectiveClicker(LogTailAdapter):
        supported_actions = frozenset({"set_ai_directive"})

        def apply(self, call):
            directive, target = call.arguments["directive"], call.arguments["target"]
            append(_llmb(f"evt|tag=GER|kind=14|detail={directive} {target}"))
            return ActionResult(ok=True, action=call.name, message="done and verified")

    append(_llmb("state|date=1939.9.1|tag=GER|pp=50|stab=60|ws=40|civ=30|mil=40|doc=10|mp=900000"))
    config = HarnessConfig(operational_control="ai", dry_run=False, require_confirmation=False)
    env = HOI4Env(DirectiveClicker(log, poll_seconds=0), config)
    env.reset()
    return env, append


def test_on_the_log_tail_reader_a_directive_is_not_observable_not_unchanged(tmp_path):
    env, append = _logtail_env(tmp_path)
    state = env.read_state()
    assert not state.known("fronts") and not state.known("wars")
    assert _directive(env, "invade", "POL").ok
    assert _directive(env, "invade", "DEN").ok

    append(_llmb("state|date=1939.9.22|tag=GER|pp=60|stab=60|ws=45|civ=30|mil=42|doc=10|mp=880000"))
    full = env.observe()
    assert not full.is_delta
    assert "NO OBSERVABLE CHANGE" not in full.brief
    expected = "effect not observable with this reader (fronts and wars unknown)"
    assert f"invade POL, raised 21d ago: {expected}" in full.brief
    assert f"invade DEN, raised 21d ago: {expected}" in full.brief

    append(_llmb("state|date=1939.9.29|tag=GER|pp=70|stab=60|ws=45|civ=30|mil=43|doc=10|mp=870000"))
    delta = env.observe()
    assert delta.is_delta
    assert "NO OBSERVABLE CHANGE" not in delta.brief
    assert f"invade POL, raised 28d ago: {expected}" in delta.brief


def test_an_unknown_baseline_is_never_diffed_against_a_known_now():
    # Fronts unknown when the directive was raised: a front appearing later is
    # not "divisions 0 -> 8", because the 0 was never measured.
    tracker = DirectiveTracker()
    tracker.raised("invade", "POL", 100,
                   GameState(date="1939-09-01", unknown_fields=["fronts", "wars"]))
    later = GameState(date="1939-09-15", wars=[War(against="POL")],
                      fronts=[Front(name="east", enemy="POL", divisions_friendly=8)])
    assert tracker.render(later)[1].strip() == (
        "invade POL, raised 14d ago: effect not observable with this reader"
        " (fronts and wars unknown)"
    )


def test_a_visible_part_still_reports_when_the_other_is_unknown():
    tracker = DirectiveTracker()
    tracker.raised("invade", "POL", 100, GameState(date="1939-09-01", unknown_fields=["fronts"]))
    later = GameState(date="1939-09-15", unknown_fields=["fronts"], wars=[War(against="POL")])
    assert tracker.render(later)[1].strip() == (
        "invade POL, raised 14d ago: now at war (fronts unknown)"
    )

    tracker = DirectiveTracker()
    tracker.raised("invade", "DEN", 100, GameState(date="1939-09-01", unknown_fields=["fronts"]))
    still = GameState(date="1939-09-15", unknown_fields=["fronts"])
    assert tracker.render(still)[1].strip() == (
        "invade DEN, raised 14d ago: effect not observable with this reader (fronts unknown)"
    )
