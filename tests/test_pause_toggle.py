"""The pause key is a toggle, and HOI4 loads paused (#64).

These run the real input driver, composite and environment against a fake game
that behaves like the real one where it matters: ``space`` toggles pause, the
date moves only while the game runs, and time passes only when someone waits.
Nothing here can send a keystroke to the machine it runs on.
"""

from __future__ import annotations

import datetime as dt

import pytest

from hoi4_harness.adapters import build_adapter
from hoi4_harness.adapters import composite as composite_module
from hoi4_harness.adapters.base import AdapterInfo, GameAdapter
from hoi4_harness.adapters.clock import days_between, wait_for_days
from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, NotFocused
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, GameState

DAYS_PER_TURN = 7
#: How long the model takes to decide. Long enough that a running game moves.
THINKING = 30.0


class Game:
    """HOI4's clock: a toggle, a date that moves only while running, fake time."""

    def __init__(self, paused: bool, seconds_per_day: float = 1.0, frozen: bool = False):
        self.paused = paused
        self.seconds_per_day = seconds_per_day
        #: A reader that cannot see the date move, like a save that is written monthly.
        self.frozen = frozen
        self.clock = 0.0
        self.ran = 0.0
        self.keys: list[str] = []

    @property
    def date(self) -> str:
        days = 0 if self.frozen else int(self.ran // self.seconds_per_day)
        return (dt.date(1936, 1, 1) + dt.timedelta(days=days)).isoformat()

    # time
    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        if not self.paused:
            self.ran += seconds

    def now(self) -> float:
        return self.clock

    # the keyboard (what pyautogui would be)
    def press(self, key: str) -> None:
        self.keys.append(key)
        if key == "space":
            self.paused = not self.paused


class Reader(GameAdapter):
    supported_actions = frozenset()

    def __init__(self, game: Game):
        self.game = game

    def info(self):
        return AdapterInfo("fake-reader", readable=True, writable=False, clock_control=False)

    def read_state(self) -> GameState:
        return GameState(date=self.game.date)

    def apply(self, call):
        return self.unsupported(call)

    def advance(self, days):
        raise NotImplementedError


def focused(title):
    return FocusCheck(True, "Hearts of Iron IV")


def driver_for(game: Game, **config) -> InputDriverAdapter:
    return InputDriverAdapter(
        InputConfig(**config), dry_run=False, focus_check=focused,
        sleep=game.sleep, gui=game,
    )


@pytest.fixture
def harness(monkeypatch):
    """An environment over the real composite and input driver, on a fake game."""

    def build(game: Game) -> HOI4Env:
        # The composite's own wait runs on the fake game's time too.
        monkeypatch.setattr(
            composite_module, "wait_for_days",
            lambda *args, **kwargs: wait_for_days(*args, sleep=game.sleep, now=game.now, **kwargs),
        )
        adapter = CompositeAdapter(Reader(game), driver_for(game), poll_seconds=0.5)
        return HOI4Env(adapter, HarnessConfig(dry_run=False, days_per_turn=DAYS_PER_TURN))

    return build


def decide(game: Game) -> None:
    """The model thinks. The game must not move while it does."""
    before = game.date
    game.sleep(THINKING)
    assert game.paused, "the game is running while the model decides"
    assert game.date == before


@pytest.mark.parametrize("starts_paused", [True, False], ids=["starts-paused", "starts-running"])
def test_the_game_is_paused_after_reset_and_after_every_advance(harness, starts_paused):
    game = Game(paused=starts_paused)
    env = harness(game)

    env.reset()
    assert game.paused, "reset() left the game running"
    decide(game)

    for turn in range(3):
        before = game.date
        env.advance()
        assert game.paused, f"advance {turn + 1} left the game running"
        assert days_between(before, game.date) == DAYS_PER_TURN, (
            f"advance {turn + 1} moved {before} -> {game.date}"
        )
        decide(game)


def test_set_game_speed_zero_leaves_a_paused_game_paused(harness):
    # Paused by a reset from running, which the pre-fix code also got right.
    game = Game(paused=False)
    env = harness(game)
    env.reset()
    assert game.paused
    presses = len(game.keys)

    result = env.act(ActionCall("set_game_speed", {"speed": 0}))

    assert result.ok, result.message
    assert len(game.keys) == presses, "the pause key was pressed on a paused game"
    decide(game)


def test_set_game_speed_zero_still_pauses_a_running_game(harness):
    game = Game(paused=False)
    env = harness(game)

    result = env.act(ActionCall("set_game_speed", {"speed": 0}))

    assert result.ok, result.message
    decide(game)


def test_the_model_pausing_mid_run_does_not_invert_the_clock(harness):
    # Starts running, so the reset leaves the clock right even before #64's fix,
    # and the only thing that could flip it is the model's own pause.
    game = Game(paused=False)
    env = harness(game)
    env.reset()
    env.act(ActionCall("set_game_speed", {"speed": 0}))
    decide(game)

    before = game.date
    env.advance()

    assert game.paused
    assert days_between(before, game.date) == DAYS_PER_TURN


def test_someone_else_pressing_the_key_between_turns_is_caught(harness):
    """The parity can flip outside the harness too: the player taps space."""
    game = Game(paused=False)
    env = harness(game)
    env.reset()
    env.advance()

    game.press("space")                      # unpaused behind the harness's back
    game.sleep(THINKING)
    before = game.date
    env.advance()

    assert game.paused
    assert days_between(before, game.date) == DAYS_PER_TURN
    decide(game)


def test_a_clock_that_cannot_be_seen_is_not_driven_blind(harness):
    """A reader whose date never moves cannot tell paused from running."""
    from hoi4_harness.adapters.input_driver import ClockUnobservable

    game = Game(paused=True, frozen=True)
    env = harness(game)
    env.reset()                              # still date: taken as paused, nothing pressed
    assert game.keys == []

    with pytest.raises(ClockUnobservable, match="did not move"):
        env.advance()
    assert game.paused, "two presses of a toggle should leave the game where it started"


def test_without_a_reader_nothing_is_pressed():
    from hoi4_harness.adapters.input_driver import ClockUnobservable

    game = Game(paused=True)
    driver = driver_for(game)
    with pytest.raises(ClockUnobservable, match="blind"):
        driver.pause()
    with pytest.raises(ClockUnobservable, match="blind"):
        driver.resume()
    result = driver.apply(ActionCall("set_game_speed", {"speed": 0}))
    assert not result.ok and result.error_kind == "rejected"
    assert game.keys == []


def test_an_unfocused_game_is_refused_before_any_waiting():
    game = Game(paused=False)
    driver = InputDriverAdapter(
        dry_run=False, sleep=game.sleep, gui=game, read_state=Reader(game).read_state,
        focus_check=lambda title: FocusCheck(False, "Firefox", reason="focused window is 'Firefox'"),
    )
    with pytest.raises(NotFocused):
        driver.pause()
    assert game.keys == [] and game.clock == 0


def test_a_dry_run_reads_nothing_and_sends_nothing():
    game = Game(paused=False)

    def no_reads():
        raise AssertionError("a dry run read the game")

    driver = InputDriverAdapter(dry_run=True, sleep=game.sleep, gui=game, read_state=no_reads)
    driver.pause()
    driver.resume()
    assert game.keys == [] and game.clock == 0
    assert driver.log == ["pause: dry run, nothing sent", "resume: dry run, nothing sent"]


def test_the_probe_window_is_configurable_end_to_end(tmp_path, monkeypatch):
    log = tmp_path / "game.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOI4_CLOCK_PROBE_SECONDS", "4.5")
    assert HarnessConfig.from_env().clock_probe_seconds == 4.5

    adapter = build_adapter(HarnessConfig(
        adapter="logtail+input", run_dir=tmp_path, log_path=log, clock_probe_seconds=3.0,
    ))
    assert adapter.writer.config.clock_probe_seconds == 3.0


def test_the_wait_counts_from_the_date_it_is_given():
    """The input driver lets a day or so pass while it watches the game start;
    those days belong to the advance."""
    game = Game(paused=False)
    reader = Reader(game)
    game.sleep(2.0)                          # two days pass before the wait begins
    state = wait_for_days(reader.read_state, DAYS_PER_TURN, sleep=game.sleep, now=game.now,
                          start="1936-01-01")
    assert days_between("1936-01-01", state.date) == DAYS_PER_TURN


def test_the_date_read_before_the_unpause_does_not_drain_events(tmp_path, monkeypatch):
    """The advance now reads the date before it unpauses. Through the log reader
    that read must peek: a war declared while the model decided is what cuts
    the wait short."""
    from hoi4_harness.adapters.logtail import LogTailAdapter

    class Keyboard(GameAdapter):
        supported_actions = frozenset()

        def __init__(self):
            self.clock_calls: list[str] = []

        def info(self):
            return AdapterInfo("keys", readable=False, writable=True, clock_control=True)

        def read_state(self):
            raise NotImplementedError

        def apply(self, call):
            return self.unsupported(call)

        def pause(self):
            self.clock_calls.append("pause")

        def resume(self, speed: int = 3):
            self.clock_calls.append("resume")

        def advance(self, days):
            raise NotImplementedError

    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v1|state|date=1936.1.1|tag=SWE|pp=1|stab=50|ws=10\n"
                   "[x]: LLMB|v1|evt|tag=SWE|kind=1|detail=war\n", encoding="utf-8")
    game = Game(paused=True)                 # only its clock: the log never moves
    monkeypatch.setattr(
        composite_module, "wait_for_days",
        lambda *args, **kwargs: wait_for_days(*args, sleep=game.sleep, now=game.now, **kwargs),
    )
    keyboard = Keyboard()
    state = CompositeAdapter(LogTailAdapter(log), keyboard, poll_seconds=0.5).advance(DAYS_PER_TURN)

    assert [e.kind for e in state.events] == ["war_declared"]
    assert keyboard.clock_calls == ["resume", "pause"]
