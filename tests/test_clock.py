"""Who owns the game clock, and what happens while we wait for it."""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from hoi4_harness.adapters.base import AdapterInfo, GameAdapter
from hoi4_harness.adapters.clock import days_between, wait_for_days
from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.input_driver import InputDriverAdapter
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, ActionResult, GameEvent, GameState


class FakeReader(GameAdapter):
    """A game whose date advances one day per observation."""

    supported_actions = frozenset()

    def __init__(self, start="1936-01-01", critical_after=None):
        self.date = dt.date.fromisoformat(start)
        self.reads = 0
        self.critical_after = critical_after

    def info(self):
        return AdapterInfo("fake-reader", readable=True, writable=False, clock_control=False)

    def read_state(self) -> GameState:
        state = GameState(date=self.date.isoformat())
        self.reads += 1
        if self.critical_after is not None and self.reads > self.critical_after:
            state.events = [GameEvent("war_declared", "war", "critical")]
        self.date += dt.timedelta(days=1)
        return state

    def apply(self, call):
        return self.unsupported(call)

    def advance(self, days):
        raise NotImplementedError


class FakeWriter(GameAdapter):
    """Records every clock instruction it is given."""

    supported_actions = frozenset({"note"})

    def __init__(self):
        self.clock_calls: list[str] = []

    def info(self):
        return AdapterInfo("fake-writer", readable=False, writable=True, clock_control=True)

    def read_state(self):
        raise NotImplementedError

    def apply(self, call):
        return ActionResult(ok=True, action=call.name, message="ok")

    def pause(self):
        self.clock_calls.append("pause")

    def resume(self, speed: int = 3):
        self.clock_calls.append(f"resume:{speed}")

    def advance(self, days):
        raise NotImplementedError


def no_sleep(_seconds: float) -> None:
    return None


# --- the waiter -------------------------------------------------------------

def test_days_between_handles_junk_without_raising():
    assert days_between("1936-01-01", "1936-01-08") == 7
    assert days_between("1936-01-01", "sometime in spring") == 0


def test_it_waits_for_the_in_game_date_not_for_one_read():
    reader = FakeReader()
    state = wait_for_days(reader.read_state, 7, sleep=no_sleep)
    assert days_between("1936-01-01", state.date) >= 7


def test_a_critical_event_ends_the_wait_early():
    reader = FakeReader(critical_after=2)
    state = wait_for_days(reader.read_state, 60, sleep=no_sleep)
    assert any(e.severity == "critical" for e in state.events)
    assert reader.reads < 60


def test_a_stalled_game_times_out_instead_of_hanging():
    """A paused game nobody unpauses must still hand back control."""
    frozen = GameState(date="1936-01-01")
    clock = iter(range(0, 10_000, 40))          # wall time races past the deadline
    state = wait_for_days(
        lambda: frozen, 7, sleep=no_sleep, now=lambda: next(clock)
    )
    assert state.date == "1936-01-01"


# --- composite, harness-owned clock -----------------------------------------

def test_the_harness_unpauses_advances_and_always_pauses_again():
    writer = FakeWriter()
    composite = CompositeAdapter(FakeReader(), writer, poll_seconds=0)
    composite.advance(3)
    assert writer.clock_calls[0].startswith("resume")
    assert writer.clock_calls[-1] == "pause"


def test_the_game_is_left_paused_even_when_the_reader_raises():
    class Exploding(FakeReader):
        def read_state(self):
            raise RuntimeError("log vanished")

    writer = FakeWriter()
    composite = CompositeAdapter(Exploding(), writer, poll_seconds=0)
    try:
        composite.advance(3)
    except RuntimeError:
        pass
    assert writer.clock_calls[-1] == "pause"


# --- composite, player-owned clock ------------------------------------------

def test_in_player_mode_the_harness_never_touches_the_clock():
    writer = FakeWriter()
    composite = CompositeAdapter(FakeReader(), writer, clock_owner="player", poll_seconds=0)
    composite.advance(5)
    composite.pause()
    composite.resume(4)
    assert writer.clock_calls == []


def test_player_mode_still_advances_by_watching():
    composite = CompositeAdapter(FakeReader(), FakeWriter(), clock_owner="player", poll_seconds=0)
    state = composite.advance(4)
    assert days_between("1936-01-01", state.date) >= 4


def test_player_mode_is_visible_in_adapter_info():
    composite = CompositeAdapter(FakeReader(), FakeWriter(), clock_owner="player", poll_seconds=0)
    assert composite.info().clock_control is False
    assert "clock: player" in composite.info().notes


# --- the harness around it --------------------------------------------------

def test_the_model_cannot_take_the_clock_in_player_mode():
    config = HarnessConfig(clock_owner="player")
    env = HOI4Env(MockAdapter(), config)
    assert "set_game_speed" not in env.allowed_actions
    assert "set_game_speed" in HOI4Env(MockAdapter(), HarnessConfig()).allowed_actions


def test_a_whitelist_cannot_smuggle_the_clock_back():
    config = HarnessConfig(clock_owner="player", enabled_actions=["set_game_speed", "note"])
    assert "set_game_speed" not in HOI4Env(MockAdapter(), config).allowed_actions


def test_reset_does_not_pause_a_game_the_player_owns():
    class Watchful(MockAdapter):
        paused = 0

        def pause(self):
            type(self).paused += 1

    Watchful.paused = 0
    HOI4Env(Watchful(), HarnessConfig(clock_owner="player")).reset()
    assert Watchful.paused == 0

    Watchful.paused = 0
    HOI4Env(Watchful(), HarnessConfig()).reset()
    assert Watchful.paused == 1


def test_speed_actions_are_still_rejected_if_one_slips_through():
    env = HOI4Env(MockAdapter(), HarnessConfig(clock_owner="player", dry_run=False))
    result = env.act(ActionCall("set_game_speed", {"speed": 5}))
    assert not result.ok and result.error_kind == "unsupported"


# --- advance_time: one clock, one rule (#65) --------------------------------
# advance_time ends a planner turn. Through the input driver it is a no-op that
# hands the clock back, and the harness then advances days_per_turn. The mock
# used to tick the requested days itself as well, so a turn there spanned
# N + days_per_turn, and the harness's advance straight after could wipe a
# critical event that had cut the first one short.


class RunningGame(GameAdapter):
    """A mock world seen through a reader: the date moves only while the game
    is unpaused, one day per poll, the way a real log tail sees it."""

    supported_actions = frozenset()

    def __init__(self, world: MockAdapter):
        self.world = world
        self.running = False
        self._polled = False

    def info(self):
        return AdapterInfo("running-game", readable=True, writable=False, clock_control=False)

    def read_state(self) -> GameState:
        if self.running and self._polled:
            self.world.advance(1)
        self._polled = True
        return copy.deepcopy(self.world.read_state())

    def apply(self, call):
        return self.unsupported(call)

    def advance(self, days):
        raise NotImplementedError


class DriverOnRunningGame(InputDriverAdapter):
    """The real input driver in dry run, its pause key wired to the fake game."""

    def __init__(self, game: RunningGame):
        super().__init__(dry_run=True, gui=object())
        self.game = game

    def pause(self) -> None:
        super().pause()
        self.game.running = False

    def resume(self, speed: int = 3) -> None:
        super().resume(speed)
        self.game.running = True
        self.game._polled = False


def advance_time(days: int) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall("t", "advance_time", {"days": days, "reason": "nothing to do"})],
        stop_reason="tool_use",
        usage=Usage(10, 10),
    )


def driver_on_running_game() -> CompositeAdapter:
    game = RunningGame(MockAdapter(seed=3))
    return CompositeAdapter(game, DriverOnRunningGame(game), poll_seconds=0)


def planner_turn_span(adapter: GameAdapter, days: int) -> int:
    """Game days one planner turn covers when the model ends it with
    advance_time(days). The first turn of a run always wakes the planner."""
    config = HarnessConfig(adapter="mock", turns=1)
    env = HOI4Env(adapter, config)
    env.reset()
    loop = AgentLoop(env=env, planner=ScriptedClient(script=[advance_time(days)]), config=config)
    report = loop.run(1)
    assert report.planner_calls == 1
    assert report.actions_ok == 1           # the advance_time call itself
    return days_between(report.start_date, report.end_date)


@pytest.mark.parametrize("days", [1, 7, 14, 30, 90])
def test_65_a_planner_turn_spans_the_same_days_on_the_mock_as_through_the_input_driver(days):
    through_the_driver = planner_turn_span(driver_on_running_game(), days)
    on_the_mock = planner_turn_span(MockAdapter(seed=3), days)
    assert through_the_driver == HarnessConfig().days_per_turn
    assert on_the_mock == through_the_driver


def test_65_a_second_advance_does_not_wipe_the_critical_event_that_stopped_the_first():
    adapter = MockAdapter(seed=1, start="1939-08-25")
    adapter.advance(90)                     # stops on 1939-09-01, the invasion
    adapter.advance(7)                      # nobody has looked yet
    events = adapter.read_state().events
    assert any(e.severity == "critical" and "Poland" in e.text for e in events)
    # Once read, they are gone on the next advance, and the old critical event
    # does not stop the new one on its first day.
    state = adapter.advance(7)
    assert not any("Poland" in e.text for e in state.events)
    assert state.date == "1939-09-15"


def test_65_sweden_from_1939_08_20_wakes_on_the_polish_invasion_while_calling_advance_time_14():
    config = HarnessConfig(adapter="mock", turns=6,
                           wake_on_free_research_slot=False, wake_on_no_focus=False)
    env = HOI4Env(MockAdapter(seed=3, start="1939-08-20"), config)
    env.reset()
    loop = AgentLoop(env=env, planner=ScriptedClient(script=[advance_time(14)] * 6),
                     config=config)
    reasons: list[str] = []
    original = loop._planner_turn

    def recording(reason):
        reasons.append(reason)
        return original(reason)

    loop._planner_turn = recording
    loop.run(6)
    assert any("critical" in r and "Poland" in r for r in reasons), reasons
