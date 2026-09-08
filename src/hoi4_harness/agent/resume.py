"""Rebuilding a run from its transcript.

A campaign is long enough that something will interrupt it -- a crash, a reboot,
Ctrl-C, a laptop lid. Everything needed to carry on is already in the transcript,
which is written as it goes; this module reads it back.

Reconstruction is deliberately tolerant. A run that died mid-write leaves a
truncated final line, and that must not be an error -- it is the normal shape of
the file this function exists to read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .llm.base import Usage
from .memory import Memory


@dataclass
class ResumeState:
    """What the harness knew when it stopped."""

    turns: int = 0
    planner_calls: int = 0
    reflex_turns: int = 0
    actions_ok: int = 0
    actions_failed: int = 0
    llm_errors: int = 0
    usage: Usage = field(default_factory=Usage)
    usd: float = 0.0
    start_date: str = ""
    last_date: str = ""
    records: int = 0
    truncated: bool = False
    #: True when the transcript predates turn_end records and turns had to be
    #: inferred from observations.
    turns_inferred: bool = False
    memory: Memory = field(default_factory=Memory)

    def summary(self) -> str:
        return (
            f"resumed at {self.last_date or 'unknown date'} after {self.turns} turns "
            f"({self.planner_calls} model calls, {self.usage.input_tokens:,} in / "
            f"{self.usage.output_tokens:,} out)"
            + (" [transcript was truncated mid-write]" if self.truncated else "")
        )


def rebuild(transcript_path: str | Path) -> ResumeState:
    """Replay a transcript into the state the loop needs to continue."""
    path = Path(transcript_path)
    state = ResumeState()
    if not path.exists():
        return state

    date, turn = "", 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # The process died mid-write. Everything before this point is
                # good; stop here rather than discarding the run.
                state.truncated = True
                break
            state.records += 1
            _apply(state, record, date_turn := _date_turn(record, date, turn))
            date, turn = date_turn

    state.last_date = date or state.last_date
    if state.turns == 0 and turn:
        # An older transcript with no turn_end records: fall back to the last
        # observed turn number, which undercounts by at most one.
        state.turns, state.turns_inferred = turn, True
    return state


def _date_turn(record: dict, date: str, turn: int) -> tuple[str, int]:
    if record.get("kind") == "observe":
        return record.get("date", date), int(record.get("turn", turn))
    if "date" in record:
        return record["date"], turn
    return date, turn


def _apply(state: ResumeState, record: dict, date_turn: tuple[str, int]) -> None:
    kind = record.get("kind")
    date, turn = date_turn

    if kind == "observe":
        if not state.start_date:
            state.start_date = date

    elif kind == "turn_end":
        state.turns = max(state.turns, int(record.get("turn", state.turns + 1)))

    elif kind == "llm":
        state.planner_calls += 1
        usage = record.get("usage") or {}
        state.usage = state.usage + Usage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_input_tokens=int(usage.get("cached_input_tokens", 0)),
        )
        for call in record.get("tool_calls") or []:
            if call.get("name") == "note":
                text = str((call.get("arguments") or {}).get("text", "")).strip()
                if text:
                    state.memory.note(date, turn, text)

    elif kind == "llm_error":
        state.llm_errors += 1

    elif kind in {"actions", "reflex"}:
        results = record.get("results") or []
        for result in results:
            # `actions` records carry tool results (is_error); `reflex` records
            # carry ActionResults (ok). Both shapes appear in one transcript.
            if "ok" in result:
                failed = not result["ok"]
            else:
                failed = bool(result.get("is_error"))
            if failed:
                state.actions_failed += 1
            else:
                state.actions_ok += 1
        if kind == "reflex":
            state.reflex_turns += 1
        names = [r.get("name") or r.get("action") for r in results]
        state.memory.record_turn(date, [n for n in names if n])

    elif kind == "skip":
        state.reflex_turns += 1

    elif kind == "run_end":
        if not state.start_date:
            state.start_date = record.get("start_date", "")
        spend = record.get("spend") or {}
        state.usd = float(spend.get("usd", state.usd))
