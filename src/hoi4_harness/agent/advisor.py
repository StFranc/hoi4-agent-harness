"""Advisor mode: the model recommends, and nothing executes.

This is the mode a person can actually use today: you play, the harness watches
through a read-only adapter, and the model says what it would do. The design
decision is to *not* reduce the model to prose. It gets the full tool menu and
makes real tool calls; this module intercepts each one, renders it as a
recommendation for the human, and never lets it reach the adapter. Concrete
calls are advice a person can follow, and they are directly comparable with the
actions a real run takes, which makes an advisor session the cheapest
evaluation the project has.

The failure mode this module exists to prevent is the model believing it acted
and planning its next turn on a change that never happened. Three things guard
against that: the tool result says plainly that the call was recorded and not
executed, the memory digest labels recommendations as such, and the env itself
refuses to act (:meth:`hoi4_harness.env.HOI4Env.act`) -- the loop asking for
advice is not what makes the mode safe; the env refusing to act is.

``note`` is the one exception. It writes the journal and changes nothing in the
game, so it is processed rather than intercepted: its continuity is what makes
the next wake's advice coherent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..types import ActionCall
from .llm.base import ToolResult

if TYPE_CHECKING:
    from .loop import Transcript
    from .memory import Memory

#: Tool result for an intercepted call. ``ok`` stays true on purpose: nothing
#: went wrong, and an error would read as a rejection and invite the model to
#: re-send the same call instead of moving on. The message is what matters --
#: it must be impossible to read as "this happened".
NOT_EXECUTED = json.dumps(
    {
        "ok": True,
        "executed": False,
        "message": (
            "Recorded as a recommendation for the player and NOT executed. "
            "The game is unchanged; do not plan as if this had happened."
        ),
    },
    sort_keys=True,
)

#: Tool result for ``note``, which really is processed: the journal is written.
NOTE_RECORDED = json.dumps(
    {
        "ok": True,
        "message": "Recorded in your journal. It will come back to you in later turns.",
    },
    sort_keys=True,
)


@dataclass
class Advisor:
    """Stands between the model and the game: records, prints, never executes."""

    transcript: Transcript
    memory: Memory

    def begin(self, date: str, turn: int, wake_reason: str) -> None:
        """Announce a wake: the date and why the model was consulted."""
        print(f"[advisor {date} | turn {turn}] {wake_reason}", flush=True)

    def prose(self, text: str) -> None:
        """Print the model's own words, flattened to one line."""
        print(f"  says: {' '.join(text.split())}", flush=True)

    def receive(
        self, action: ActionCall, *, date: str, turn: int, wake_reason: str
    ) -> ToolResult:
        """Handle one tool call in advisor mode. Nothing here executes anything."""
        if action.name == "note":
            return self._note(action, date, turn)
        return self._recommend(action, date, turn, wake_reason)

    def _recommend(
        self, action: ActionCall, date: str, turn: int, wake_reason: str
    ) -> ToolResult:
        self.transcript.write(
            "recommendation",
            date=date,
            turn=turn,
            wake_reason=wake_reason,
            call_id=action.call_id,
            name=action.name,
            arguments=action.arguments,
        )
        print(f"  would: {_render(action)}", flush=True)
        return ToolResult(call_id=action.call_id, name=action.name, content=NOT_EXECUTED)

    def _note(self, action: ActionCall, date: str, turn: int) -> ToolResult:
        self.memory.note(date, turn, str(action.arguments.get("text", "")))
        return ToolResult(call_id=action.call_id, name=action.name, content=NOTE_RECORDED)


def _render(action: ActionCall) -> str:
    """One human-readable line: the call and its arguments, in full."""
    args = ", ".join(f"{key}={json.dumps(value, default=str)}" for key, value in action.arguments.items())
    return f"{action.name}({args})"
