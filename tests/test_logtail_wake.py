"""A war declaration read from game.log wakes the planner, and only once (#62).

The log-tail reader is the only adapter that queues events, so it is the only
one where *which read* sees an event decides whether the wake rule does. These
tests run the real loop over the real reader, against a fake game that writes
the mod's telemetry the way a running game does: a day per poll while it runs,
nothing while it is paused.
"""

from __future__ import annotations

import datetime as dt
import json

from hoi4_harness.adapters.base import AdapterInfo, GameAdapter
from hoi4_harness.adapters.clock import days_between
from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionResult

STATE = (
    "[messagehandler.cpp:290]: LLMB|v1|state|date={date}|tag=SWE|pp=42|stab=63|ws=18"
    "|civ=12|mil=5|doc=4|mp=214000\n"
)
WAR = "[messagehandler.cpp:290]: LLMB|v1|evt|tag=SWE|kind=1|detail=Germany declared war\n"
WAR_WAKE = "critical event: Germany declared war"


class Game:
    """HOI4 as the log reader sees it."""

    def __init__(self, log, start="1936-01-01", events=None, running=False):
        self.log = log
        self.day = dt.date.fromisoformat(start)
        #: iso date -> extra lines the mod prints that day, after its state line
        self.events = dict(events or {})
        self.running = running
        self._write(self._state_line())

    def _state_line(self) -> str:
        return STATE.format(date=f"{self.day.year}.{self.day.month}.{self.day.day}")

    def _write(self, *lines: str) -> None:
        with self.log.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)

    def tick(self) -> None:
        if not self.running:
            return
        self.day += dt.timedelta(days=1)
        self._write(self._state_line(), *self.events.pop(self.day.isoformat(), []))

    def declare_war(self) -> None:
        """Now, whatever the date: what the AI does while the model is thinking."""
        self._write(WAR)


class GameLog(LogTailAdapter):
    """The real reader. Each poll lets the game run first, as the real one does
    while the harness waits on the clock."""

    def __init__(self, game: Game):
        super().__init__(game.log, poll_seconds=0)
        self.game = game

    def poll(self) -> int:
        self.game.tick()
        return super().poll()


class Keyboard(GameAdapter):
    """The writer half: it starts and stops the game, and does nothing else."""

    supported_actions = frozenset({"note", "advance_time"})

    def __init__(self, game: Game):
        self.game = game

    def info(self) -> AdapterInfo:
        return AdapterInfo("keyboard", readable=False, writable=True, clock_control=True)

    def read_state(self):
        raise NotImplementedError

    def apply(self, call):
        return ActionResult(ok=True, action=call.name, call_id=call.call_id, message="ok")

    def pause(self) -> None:
        self.game.running = False

    def resume(self, speed: int = 3) -> None:
        self.game.running = True

    def advance(self, days):
        raise NotImplementedError


def play(tmp_path, game: Game, turns: int, planner=None, resume=False, **config):
    config = HarnessConfig(turns=turns, **config)
    adapter = CompositeAdapter(GameLog(game), Keyboard(game),
                               clock_owner=config.clock_owner, poll_seconds=0)
    env = HOI4Env(adapter, config)
    env.reset()
    transcript = tmp_path / "transcript.jsonl"
    loop = AgentLoop(env=env, planner=planner or ScriptedClient(), config=config,
                     transcript_path=transcript, resume=resume)
    loop.run(turns)
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    return loop, records


def wakes(records) -> list[dict]:
    return [r for r in records if r["kind"] == "observe"]


def test_a_war_that_cuts_the_clock_wait_short_wakes_the_planner_next_turn(tmp_path):
    """The acceptance case: the war lands while the harness waits on the clock."""
    game = Game(tmp_path / "game.log", events={"1936-01-05": [WAR]})
    loop, records = play(tmp_path, game, turns=3)

    first_turn = next(r for r in records if r["kind"] == "turn_end")
    assert (first_turn["from_date"], first_turn["date"]) == ("1936-01-01", "1936-01-05")

    observed = wakes(records)
    assert [r["wake_reason"] for r in observed][:2] == ["scheduled review", WAR_WAKE]
    woken = observed[1]
    assert woken["date"] == "1936-01-05"
    assert "Germany declared war" in woken["brief"]
    assert "Since last turn:" in woken["brief"]


def test_one_war_declaration_wakes_the_planner_once(tmp_path):
    game = Game(tmp_path / "game.log", events={"1936-01-05": [WAR]})
    loop, records = play(tmp_path, game, turns=4)

    reasons = [r["wake_reason"] for r in wakes(records)]
    assert reasons.count(WAR_WAKE) == 1
    later = [r["brief"] for r in wakes(records)[2:]]
    assert not any("Germany declared war" in brief for brief in later)
    # The wait after the war wake runs its full week: it is not cut short again
    # by the event that turn already answered.
    ends = [r for r in records if r["kind"] == "turn_end"]
    assert ends[1]["from_date"] == "1936-01-05"
    assert days_between(ends[1]["from_date"], ends[1]["date"]) >= 7


def test_a_war_declared_while_the_model_thinks_is_not_lost(tmp_path):
    """Player-owned clock: the game never stops, so the war can land after the
    turn's own read. It must still reach the wake rule on the next turn."""
    game = Game(tmp_path / "game.log", running=True)

    class ThinkingSlowly(ScriptedClient):
        def complete(self, system, messages, tools=None, max_tokens=None):
            if len(self.calls) == 0:
                game.declare_war()
            return super().complete(system, messages, tools, max_tokens)

    loop, records = play(tmp_path, game, turns=2, planner=ThinkingSlowly(),
                         clock_owner="player")

    observed = wakes(records)
    assert "Germany declared war" not in observed[0]["brief"]   # it had not happened yet
    assert [r["wake_reason"] for r in observed][:2] == ["scheduled review", WAR_WAKE]
    assert "Germany declared war" in observed[1]["brief"]


def test_a_war_the_brief_already_showed_does_not_wake_the_planner_again(tmp_path):
    """Player-owned clock: the war lands between the wake rule's read and the
    brief's. The model saw it in that brief, so it is answered, and the next turn
    must not wake for it a second time."""
    game = Game(tmp_path / "game.log", events={"1936-01-05": [WAR]}, running=True)
    loop, records = play(tmp_path, game, turns=3, clock_owner="player")

    observed = wakes(records)
    assert observed[0]["wake_reason"] == "scheduled review"     # judged on 01-04
    assert "Germany declared war" in observed[0]["brief"]       # read on 01-05
    assert WAR_WAKE not in [r["wake_reason"] for r in observed]


def test_events_already_in_the_log_at_attach_are_history_not_news(tmp_path):
    """The reader starts at the top of game.log, so its first read takes in every
    event the mod has logged this session. Starting the harness, resuming it, or
    attaching to a game a person has been playing must not report those as news."""
    log = tmp_path / "game.log"
    log.write_text(STATE.format(date="1935.12.20") + WAR, encoding="utf-8")
    game = Game(log, start="1936-01-01")
    loop, records = play(tmp_path, game, turns=2)

    observed = wakes(records)
    assert observed[0]["wake_reason"] == "scheduled review"
    assert "Germany declared war" not in observed[0]["brief"]
    assert WAR_WAKE not in [r["wake_reason"] for r in observed]


def test_a_resumed_run_does_not_wake_again_for_a_war_already_answered(tmp_path):
    """``play --resume``: a new reader re-reads game.log from the top, war included."""
    game = Game(tmp_path / "game.log", events={"1936-01-05": [WAR]})
    play(tmp_path, game, turns=3)
    loop, records = play(tmp_path, game, turns=2, resume=True)

    resumed_at = next(i for i, r in enumerate(records) if r["kind"] == "resumed")
    after = wakes(records[resumed_at:])
    assert WAR_WAKE not in [r["wake_reason"] for r in after]
    assert not any("Germany declared war" in r["brief"] for r in after)
