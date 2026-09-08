"""Who owns the game clock, and what happens while we wait for it."""

from __future__ import annotations

import datetime as dt

from hoi4_harness.adapters.base import AdapterInfo, GameAdapter
from hoi4_harness.adapters.clock import days_between, wait_for_days
from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.mock import MockAdapter
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
