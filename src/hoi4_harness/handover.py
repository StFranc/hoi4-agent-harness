"""Action handover: the harness must not type over the player (#33).

With a person playing, synthetic input lands in whatever panel they have open,
at whatever moment the loop decided to act. The window-focus guard does not
help -- the game *is* focused, it is just in use. Two people cannot share a
keyboard, so this is turn-taking:

* the agent decides on its own schedule, and every action it takes is
  **queued**, not executed -- the tool result says so, so it does not plan on
  a change that has not happened;
* the queue runs only when the player **grants** a window, and stops the moment
  they take it back, leaving whatever had not run still queued;
* each action is **re-checked** before it runs, because the world moved while
  it waited: a focus queued last week may be pointless now that one is running.
  Those are reported as stale, never clicked blindly -- the same "act, verify,
  report" contract as the input driver;
* every window is recorded: when it was granted, what ran, what went stale and
  how long each action waited. A model whose plans routinely go stale before
  they run is saying the handover cadence is wrong.

The grant is a file. ``hoi4-harness handover`` creates it (bind that to a hotkey
with whatever your OS offers) and the harness deletes it when the queue has
run; deleting it yourself mid-run takes the keyboard back. A file is the one
signal every platform, every terminal and every hotkey tool can produce without
a keyboard hook in this process, which would itself be input contention.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .types import ActionCall, ActionResult, GameState

GRANT_FILENAME = "handover.grant"
QUEUE_FILENAME = "handover-queue.json"

#: Never queued: they change nothing the player could be clicking on.
PASS_THROUGH = frozenset({"note", "advance_time"})


@dataclass
class Pending:
    call: ActionCall
    queued_on: str          # game date
    queued_turn: int
    position: int


@dataclass
class HandoverReport:
    """One granted window, for the transcript and the next brief."""

    granted_on: str
    ran: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    aborted: bool = False
    left_queued: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        bits = [f"{len(self.ran)} ran"]
        if self.stale:
            bits.append(f"{len(self.stale)} stale: " + "; ".join(
                f"{s['action']} ({s['reason']})" for s in self.stale[:3]))
        if self.failed:
            bits.append(f"{len(self.failed)} failed: " + "; ".join(
                f"{f['action']} ({f['reason']})" for f in self.failed[:3]))
        if self.aborted:
            bits.append(f"player took control back, {self.left_queued} still queued")
        return f"Handover on {self.granted_on}: " + ", ".join(bits)


def stale_reason(call: ActionCall, state: GameState) -> str | None:
    """Why a queued action no longer applies, judged against the state now.

    Only checks the harness can make from ``GameState`` without guessing; the
    adapter's own verification catches the rest when the action runs.
    """
    args = call.arguments
    if call.name == "set_national_focus" and state.known("national_focus") and state.national_focus:
        return f"'{state.national_focus}' is already running"
    if call.name == "set_national_focus" and args.get("focus_id") in state.completed_focuses:
        return "that focus is already complete"
    if call.name == "start_research" and state.known("research"):
        if not any(slot.technology is None for slot in state.research):
            return "no research slot is free any more"
        if any(slot.technology == args.get("technology") for slot in state.research):
            return "already being researched"
    if call.name == "set_production" and state.known("military_factories"):
        others = sum(line.factories for line in state.production
                     if line.equipment != args.get("equipment"))
        if others + int(args.get("factories", 0)) > state.military_factories:
            return "not enough free military factories any more"
    return None


class HandoverQueue:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.pending: list[Pending] = []
        self.reports: list[HandoverReport] = []
        self._next = 1

    # --- the grant ------------------------------------------------------------

    @property
    def grant_path(self) -> Path:
        return self.run_dir / GRANT_FILENAME

    def granted(self) -> bool:
        return self.grant_path.exists()

    def grant(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.grant_path.touch()

    def _release(self) -> None:
        try:
            self.grant_path.unlink()
        except FileNotFoundError:
            pass

    # --- queueing -------------------------------------------------------------

    def enqueue(self, call: ActionCall, date: str, turn: int) -> ActionResult:
        # A queued action changes nothing until the window opens, so whatever
        # asked for it (a reflex, most often) asks again next turn. Queue it
        # once: running the same call N times is not what anyone decided.
        for entry in self.pending:
            if entry.call.name == call.name and entry.call.arguments == call.arguments:
                return ActionResult(
                    ok=True,
                    action=call.name,
                    call_id=call.call_id,
                    message=(
                        f"Already queued as #{entry.position} for the player's next handover. "
                        "NOT done yet. Do not plan as if it had happened."
                    ),
                    changed={"queued": entry.position, "executed": False},
                )
        entry = Pending(call=call, queued_on=date, queued_turn=turn, position=self._next)
        self._next += 1
        self.pending.append(entry)
        self._publish()
        return ActionResult(
            ok=True,
            action=call.name,
            call_id=call.call_id,
            message=(
                f"Queued as #{entry.position} for the player's next handover. NOT done "
                "yet: it runs only when the player grants control, and is re-checked "
                "then. Do not plan as if it had happened."
            ),
            changed={"queued": entry.position, "executed": False},
        )

    def _publish(self) -> None:
        """The visible indicator: what the harness is about to do, in a file the
        player (or an overlay) can read at any moment."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = [
            {"position": p.position, "action": p.call.name, "arguments": p.call.arguments,
             "queued_on": p.queued_on, "rationale": p.call.rationale}
            for p in self.pending
        ]
        (self.run_dir / QUEUE_FILENAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def describe(self) -> list[str]:
        if not self.pending:
            return []
        items = ", ".join(f"#{p.position} {p.call.name}" for p in self.pending[:6])
        more = f" (+{len(self.pending) - 6})" if len(self.pending) > 6 else ""
        return [f"Queued for handover ({len(self.pending)}): {items}{more}"]

    # --- the window -----------------------------------------------------------

    def run(
        self,
        read_state: Callable[[], GameState],
        apply: Callable[[ActionCall], ActionResult],
    ) -> HandoverReport | None:
        """Run the queue if the player has granted a window. None if not."""
        if not self.granted():
            return None
        report = HandoverReport(granted_on=read_state().date)
        while self.pending:
            if not self.granted():
                # The player took the keyboard back: stop between actions and
                # keep the rest, rather than finishing what we started.
                report.aborted = True
                break
            entry = self.pending.pop(0)
            state = read_state()
            waited = {"action": entry.call.name, "arguments": entry.call.arguments,
                      "queued_on": entry.queued_on, "ran_on": state.date}
            reason = stale_reason(entry.call, state)
            if reason:
                report.stale.append({**waited, "reason": reason})
                continue
            result = apply(entry.call)
            if result.ok:
                report.ran.append(waited)
            else:
                report.failed.append({**waited, "reason": result.message})
        report.left_queued = len(self.pending)
        if not report.aborted:
            self._release()
        self._publish()
        self.reports.append(report)
        return report
