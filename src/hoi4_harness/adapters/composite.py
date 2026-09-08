"""Glue one read-capable adapter to one write-capable adapter.

Every realistic configuration is a split: a log-tail or savegame reader with an
input-driver writer. Nothing observes and acts through the same channel except
the mock.

The interesting part is the clock. When the harness is the only player it drives
the game forward itself; when a person is playing, it must not touch the clock at
all -- being paused mid-battle by your own tooling is worse than having no
tooling. That is what ``clock_owner`` decides.
"""

from __future__ import annotations

from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter
from .clock import wait_for_days


class CompositeAdapter(GameAdapter):
    def __init__(
        self,
        reader: GameAdapter,
        writer: GameAdapter,
        clock_owner: str = "harness",
        poll_seconds: float = 0.5,
    ):
        self.reader = reader
        self.writer = writer
        self.clock_owner = clock_owner
        self.poll_seconds = poll_seconds
        self.supported_actions = writer.supported_actions

    @property
    def owns_clock(self) -> bool:
        return self.clock_owner == "harness"

    def info(self) -> AdapterInfo:
        r, w = self.reader.info(), self.writer.info()
        return AdapterInfo(
            name=f"{r.name}+{w.name}",
            readable=r.readable,
            writable=w.writable,
            clock_control=w.clock_control and self.owns_clock,
            notes=f"clock: {self.clock_owner} | read: {r.notes} | write: {w.notes}",
        )

    def read_state(self) -> GameState:
        return self.reader.read_state()

    def apply(self, call: ActionCall) -> ActionResult:
        return self.writer.apply(call)

    def pause(self) -> None:
        if self.owns_clock:
            self.writer.pause()

    def resume(self, speed: int = 3) -> None:
        if self.owns_clock:
            self.writer.resume(speed)

    def advance(self, days: int) -> GameState:
        """Run the game forward ``days`` in-game days, then pause.

        Blocks on the *in-game* date rather than returning after one read: with a
        real bridge the previous behaviour took a turn every few milliseconds and
        would burn a budget in seconds. Returns early on a critical event, and
        leaves the game paused on every exit path including the timeout -- when
        the harness owns the clock.

        When the player owns it, this only watches: no pause, no resume, no
        speed change.
        """
        if not self.owns_clock:
            return wait_for_days(self.reader.read_state, days, self.poll_seconds)

        self.writer.resume()
        try:
            return wait_for_days(self.reader.read_state, days, self.poll_seconds)
        finally:
            self.writer.pause()

    def close(self) -> None:
        self.reader.close()
        self.writer.close()
