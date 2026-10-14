import datetime as dt

import pytest

from hoi4_harness.actions import catalog
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import BudgetConfig, HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import GameState


def make_loop(script=None, **overrides):
    config = HarnessConfig(adapter="mock", turns=6, **overrides)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=ScriptedClient(script=script), config=config), env


def test_a_run_advances_the_calendar_and_takes_actions():
    loop, env = make_loop()
    report = loop.run(6)
    assert report.turns == 6
    assert report.start_date < report.end_date
    assert report.actions_ok > 0


def test_irreversible_actions_are_blocked_in_dry_run():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "diplomacy", {"action": "declare_war", "target": "GER"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(1)
    assert loop.report.actions_failed >= 1


def test_the_local_validator_still_guards_a_client_claiming_strict_support():
    """Strict tool schemas are the provider promising well-formed calls; the
    loop does not take the promise. The same malformed call is rejected here as
    it would be on a provider without strict."""
    script = [
        LLMResponse(
            tool_calls=[
                ToolCall(
                    "1",
                    "queue_construction",
                    {"building": "civilian_factory", "state": "capital", "turbo": True},
                )
            ],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(
            tool_calls=[ToolCall("2", "advance_time", {"days": 7, "reason": "turn over"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
    ]
    loop, env = make_loop(script=script)
    loop.planner.supports_strict_tools = True    # a strict-capable provider
    loop.run(1)
    assert loop.report.actions_failed == 1       # caught locally, not by the provider


def test_the_budget_guard_downgrades_to_reflex_instead_of_stopping():
    loop, env = make_loop()
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    report = loop.run(5)
    assert report.planner_calls <= 1
    assert report.reflex_turns >= 3      # the rest ran for free
    assert report.turns == 5             # and the game kept moving


def test_notes_written_by_the_agent_come_back_in_the_next_prompt():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "note", {"text": "Build civs until 1938."})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(2)
    assert any("Build civs until 1938." in e.text for e in loop.memory.journal)
    assert "Build civs until 1938." in loop.memory.context_block()


def test_the_planner_is_not_woken_when_nothing_is_happening():
    from hoi4_harness.agent.policy import Policy

    quiet = GameState(national_focus="industrial_effort")
    quiet.research = []
    assert not Policy(days_per_turn=7).should_wake(quiet, days_since_planner=1).wake
    assert Policy(days_per_turn=7).should_wake(quiet, days_since_planner=7).wake


# --- the scheduled review, through the loop's own counter (#63) ---------------


class _Holds(ScriptedClient):
    """A planner that answers every wake by doing nothing: no action of its
    own can wake it again or move the clock."""

    def complete(self, system, messages, tools=None, max_tokens=None):
        return LLMResponse(text="Holding.", usage=Usage(10, 10))


class _StoppedClock(MockAdapter):
    """A reader that has lost the calendar: the game runs, the date never moves."""

    def advance(self, days):
        return self.read_state()


class _ShortTurns(MockAdapter):
    """A clock that comes back after three days whatever it was asked for, as
    an early return or a wait that ran out would."""

    def advance(self, days):
        return super().advance(min(days, 3))


def _wakes_on_a_quiet_run(turns, adapter=None, **overrides):
    """(date, decision) for every turn of a run where only the schedule can
    wake the planner: opportunity wakes off, peacetime, a planner that holds."""
    config = HarnessConfig(
        adapter="mock", wake_on_no_focus=False, wake_on_free_research_slot=False, **overrides
    )
    env = HOI4Env(adapter or MockAdapter(seed=3), config)
    env.reset()
    loop = AgentLoop(env=env, planner=_Holds(), config=config)
    seen = []
    decide = loop.policy.should_wake

    def spy(state, days_since_planner, previous=None):
        decision = decide(state, days_since_planner, previous)
        seen.append((state.date, decision))
        return decision

    loop.policy.should_wake = spy
    loop.run(turns)
    return seen, loop.report


def _days_between_wakes(seen):
    woke = [dt.date.fromisoformat(date) for date, decision in seen if decision.wake]
    return [(later - earlier).days for earlier, later in zip(woke, woke[1:], strict=False)]


@pytest.mark.parametrize("days_per_turn", [3, 7, 14])
def test_the_scheduled_review_comes_round_every_days_per_turn(days_per_turn):
    """The counter used to be reset on a wake and topped up only on reflex
    turns, so the turn after every review saw 0 days and the review came round
    every other turn: every 14 game days at the default 7."""
    seen, report = _wakes_on_a_quiet_run(12, days_per_turn=days_per_turn)

    assert [d.reason for _, d in seen] == ["scheduled review"] * 12
    assert _days_between_wakes(seen) == [days_per_turn] * 11
    assert report.planner_calls == 12 and report.reflex_turns == 0


def test_the_review_counts_game_days_not_turns():
    """A turn that ran fewer days than it asked for is counted as it ran, so the
    review still waits for days_per_turn of game time."""
    seen, _ = _wakes_on_a_quiet_run(9, adapter=_ShortTurns(seed=3), days_per_turn=7)

    assert [d.wake for _, d in seen] == [True, False, False] * 3
    assert _days_between_wakes(seen) == [9, 9]


def test_a_calendar_that_cannot_be_read_does_not_stop_the_review():
    """No date movement to measure: each turn counts the days it asked for,
    rather than none, which would leave the planner asleep for good."""
    seen, _ = _wakes_on_a_quiet_run(6, adapter=_StoppedClock(seed=3), days_per_turn=7)

    assert [d.wake for _, d in seen] == [True] * 6


def test_memory_is_dated_when_the_turn_happened_not_after_it():
    """Adapters hand back a live GameState that keeps mutating as the turn's
    actions run, so reading the date at the end of the turn dated every journal
    entry after whatever advance_time the model called."""
    loop, env = make_loop()
    start = env.read_state().date
    loop.run(3)

    observed_dates = [entry.split(":")[0] for entry in loop.memory.digest]
    assert observed_dates[0] == start
    # Turns are days_per_turn apart in the mock; nothing should be dated between.
    assert len(set(observed_dates)) == len(observed_dates)
    for entry, expected in zip(loop.memory.digest, observed_dates, strict=True):
        assert entry.startswith(expected)


def test_a_note_is_journalled_under_the_date_of_the_turn_that_wrote_it():
    script = [
        LLMResponse(
            tool_calls=[
                ToolCall("1", "note", {"text": "Plan set."}),
                ToolCall("2", "advance_time", {"days": 30}),
            ],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    start = env.read_state().date
    loop.run(1)
    assert loop.memory.journal[0].date == start
def test_a_wake_turn_leaves_a_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert lines[0].startswith("1936-01-01")
    assert "wake: no national focus is running" in lines[0]
    # Which actions a wake produces depends on what the brief showed, so assert
    # the line reports some and carries the token and cost columns.
    assert any(name in lines[0] for name in (spec.name for spec in catalog.ACTIONS))
    assert " tok" in lines[0]
    assert lines[0].endswith("$0.00")


def test_a_reflex_turn_leaves_a_progress_line_without_a_token_column():
    lines: list[str] = []
    # Reflex-only, because with the opportunity wakes merely off the scheduled
    # review takes every quiet turn: the review interval is the turn length.
    loop, _env = make_loop(planner_enabled=False)
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert "wake:" not in lines[1]        # a reflex turn is not a wake
    assert " tok" not in lines[1]         # and spends no tokens
    assert lines[1].endswith("$0.00")


def test_a_budget_downgrade_says_why_in_its_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    loop.run(2)
    assert "reflex (budget: call ceiling reached (1))" in lines[1]
    assert "wake:" not in lines[1]
