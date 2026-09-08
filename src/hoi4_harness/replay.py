"""Replay one decision point from a transcript.

A run's transcript records what the model saw and what it did about it, but not
the conversation it was handed: the system prompt, the user message and the tool
menu are rebuilt from config on every turn and never written down. That makes
"would a different model have made the same call here?" unanswerable after the
fact.

Replay closes the gap. It re-derives the prompt a turn was given -- with the
same builders the loop uses (:func:`turn_prompt` and :func:`build_system`), and
the memory block reconstructed from the note calls and action records around
that turn -- sends it to a model, and prints the original and new responses side
by side.

The contract is read-only, and structurally so: the transcript is opened for
reading, the adapter is only ever asked for its capability set, and the tool
calls the new model returns are printed, never executed. Two approximations
remain, both inherent to the format rather than choices: memory is rebuilt from
what the transcript shows, so a run resumed onto an existing ``memory.json`` had
earlier context replay cannot see; and the prompt reflects the *current* config,
so reproducing a run exactly means passing the flags it ran with. The second is
a feature as often as a bug -- same situation, different objective or guidance,
is half the point.
"""

from __future__ import annotations

import itertools
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actions import catalog, registry
from .actions.registry import ToolSpec
from .adapters import build_adapter
from .agent.llm import build_llm
from .agent.llm.base import LLMClient, LLMResponse, Msg
from .agent.memory import Memory
from .agent.prompts import build_system, turn_prompt
from .config import HarnessConfig

_COLUMN_WIDTH = 52  # per comparison column, so the pair fits a 110-char terminal


class ReplayError(Exception):
    """Replay cannot proceed. The CLI prints this message; it never tracebacks."""


@dataclass
class RebuiltPrompt:
    """The conversation one turn was given, re-derived after the fact."""

    system: str
    user: str
    tools: list[ToolSpec]


@dataclass
class ReplayOutcome:
    """One replay: the rebuilt prompt, the original turn, and the new response."""

    record: dict[str, Any]                # the transcript's observe record
    prompt: RebuiltPrompt
    original_model: str
    original_calls: list[dict[str, Any]]  # what the original model called
    original_text: str
    replayed_as: str                      # provider:model that answered this time
    response: LLMResponse

    def render(self) -> str:
        original = _column(
            [_render_call(c.get("name", ""), c.get("arguments") or {}) for c in self.original_calls],
            self.original_text,
            empty="(no recorded response)" if not self.original_model else "(no tool calls)",
        )
        replayed = _column(
            [_render_call(c.name, c.arguments) for c in self.response.tool_calls],
            self.response.text,
            empty="(no tool calls)",
        )
        usage = self.response.usage
        return "\n".join(
            [
                f"replay of turn {self.record.get('turn')} -- {self.record.get('date')} -- "
                f"woken because: {self.record.get('wake_reason', '')}",
                f"original model: {self.original_model or 'unknown'} | "
                f"replayed with: {self.replayed_as} | {len(self.prompt.tools)} tools offered",
                "read-only: nothing below is executed, the game is never contacted",
                "",
                _side_by_side(
                    f"original ({self.original_model or '?'})",
                    original,
                    f"replayed ({self.replayed_as})",
                    replayed,
                ),
                "",
                f"stop_reason {self.response.stop_reason} | "
                f"{usage.input_tokens} in / {usage.output_tokens} out tokens",
            ]
        )


def load(path: Path) -> list[dict[str, Any]]:
    """Parse a transcript JSONL into records.

    A transcript is append-only JSONL written by a process that can die
    mid-write, so the tail is legitimately untrustworthy: a final line cut off
    mid-record, stray blank lines. Any line that does not parse is skipped
    rather than failing the whole replay.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayError(f"cannot read {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def list_turns(path: Path) -> str:
    """The turns a transcript offers, for choosing a ``--turn``."""
    rows = [
        f"{record.get('turn', '?'):>4}  {str(record.get('date', '?')):<10}  "
        f"{'delta' if record.get('is_delta') else 'full':<5}  {record.get('wake_reason', '')}"
        for record in _observe_records(load(path))
    ]
    header = f"{'turn':>4}  {'date':<10}  {'brief':<5}  wake reason"
    return "\n".join([header, *rows])


def replay_turn(
    path: Path,
    turn: int,
    config: HarnessConfig,
    planner: LLMClient | None = None,
) -> ReplayOutcome:
    """Send one rebuilt prompt to ``planner`` (default: the configured provider).

    The response comes back rendered and untouched: it is never executed, and
    the transcript is only ever read.
    """
    records = load(path)
    record = find_turn(records, turn)
    prompt = rebuild_prompt(config, records, record)
    original_model, original_calls, original_text = _first_response(records, turn)
    client = planner or build_llm(config.planner)
    response = client.complete(prompt.system, [Msg(role="user", text=prompt.user)], prompt.tools)
    return ReplayOutcome(
        record=record,
        prompt=prompt,
        original_model=original_model,
        original_calls=original_calls,
        original_text=original_text,
        replayed_as=f"{client.name}:{client.model}",
        response=response,
    )


def find_turn(records: list[dict[str, Any]], turn: int) -> dict[str, Any]:
    turns = _observe_records(records)
    for record in turns:
        if record.get("turn") == turn:
            return record
    present = ", ".join(str(record.get("turn")) for record in turns)
    raise ReplayError(f"no turn {turn} in this transcript. Planner turns present: {present}")


def rebuild_prompt(
    config: HarnessConfig,
    records: list[dict[str, Any]],
    record: dict[str, Any],
) -> RebuiltPrompt:
    """Re-derive the system prompt, user message and tool menu for one turn.

    The same calls the agent loop makes, fed from the transcript's observe
    record plus the current config instead of a live environment.
    """
    turn = int(record.get("turn", 0))
    memory = memory_before(records, turn)
    system = build_system(
        guidance=config.guidance,
        extra=config.system_prompt_extra,
        system_prompt_path=config.system_prompt_path,
        operational_control=config.operational_control,
    )
    user = turn_prompt(
        brief=str(record.get("brief", "")),
        objective=config.objective,
        memory_block=memory.context_block(),
        wake_reason=str(record.get("wake_reason", "")),
        turn=turn,
        actions_left=config.max_actions_per_turn,
    )
    return RebuiltPrompt(system=system, user=user, tools=registry.tool_specs(_allowed_actions(config)))


def memory_before(records: list[dict[str, Any]], turn: int) -> Memory:
    """Rebuild the memory block the prompt at ``turn`` was built from.

    The transcript does not record the prompt, but it records everything memory
    is made of: each ``note`` call (in the llm records) and the actions every
    earlier planner turn took (the actions records, whose ``is_error`` mirrors
    the loop's rejection marks). Replaying those through the real
    :class:`Memory` reproduces the bounded journal and digest exactly -- for a
    run that started from empty memory. A run resumed onto an existing
    ``memory.json`` had earlier context this transcript cannot show.
    """
    memory = Memory()
    date: str | None = None  # the planner turn currently being replayed
    turn_no = 0
    taken: list[str] = []
    for record in records:
        kind = record.get("kind")
        if kind == "observe":
            # Flush the previous planner turn's digest first: it was recorded
            # before the target turn's prompt was built, so it belongs in it.
            if date is not None:
                memory.record_turn(date, taken)
            if record.get("turn") == turn:
                break  # the target turn's own actions must not be in its prompt
            date, turn_no, taken = str(record.get("date", "")), int(record.get("turn") or 0), []
        elif date is None:
            continue
        elif kind == "llm":
            # The loop journals a note before executing it, so every note call
            # in an llm record reached memory, whatever happened to the result.
            for call in record.get("tool_calls") or []:
                if call.get("name") == "note":
                    text = call.get("arguments", {}).get("text", "")
                    memory.note(date, turn_no, str(text))
        elif kind == "actions":
            for result in record.get("results") or []:
                name = str(result.get("name") or "?")
                taken.append(name + (" (rejected)" if result.get("is_error") else ""))
    return memory


def _observe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The observe records: one per planner turn, in order."""
    turns = [record for record in records if record.get("kind") == "observe"]
    if not turns:
        raise ReplayError("this transcript has no planner turns -- was it written by play or eval?")
    return turns


def _first_response(
    records: list[dict[str, Any]], turn: int
) -> tuple[str, list[dict[str, Any]], str]:
    """The original model's first response that turn: (model, tool calls, text).

    Only the first. Later rounds reacted to tool results replay never
    re-creates, so holding them up against a single fresh response would not be
    a comparison.
    """
    seen = False
    for record in records:
        kind = record.get("kind")
        if kind == "observe":
            if seen:
                break
            seen = record.get("turn") == turn
        elif seen and kind == "llm":
            return (
                str(record.get("model") or ""),
                list(record.get("tool_calls") or []),
                str(record.get("text") or ""),
            )
    return "", [], ""


def _allowed_actions(config: HarnessConfig) -> set[str]:
    """The tool menu, filtered the way a live run filters it.

    The same three filters ``HOI4Env.allowed_actions`` applies, in the same
    order: what the adapter can do, the control mode, the operator's allow/deny
    lists. The adapter is constructed for its capability set alone -- replay
    never reads state from it or sends it anything. If it cannot even be
    constructed (a missing optional dependency, say), the whole catalog is
    offered rather than failing a read-only command.
    """
    try:
        supports = set(build_adapter(config).supported_actions)
    except Exception:  # noqa: BLE001 - replay must survive a half-installed config
        supports = {spec.name for spec in catalog.ACTIONS}
    return config.allowed_actions(supports)


def _render_call(name: str, arguments: dict[str, Any]) -> str:
    """``name(arg=value, ...)``. Values are JSON-quoted so the string "7" does
    not read as the number 7, and sorted so the two columns line up."""
    listed = ", ".join(f"{k}={json.dumps(v, sort_keys=True)}" for k, v in sorted(arguments.items()))
    return f"{name or '?'}({listed})"


def _wrap(text: str) -> list[str]:
    wrapped = textwrap.wrap(text, width=_COLUMN_WIDTH, subsequent_indent="  ", break_long_words=True)
    return wrapped or [""]


def _wrapped(lines: list[str]) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line))
    return wrapped


def _column(calls: list[str], text: str, empty: str) -> list[str]:
    """One comparison column: each call wrapped to the width, then its prose.

    Nothing is truncated -- an argument longer than the column flows onto the
    next row, because a comparison that silently drops an argument is worse
    than a tall table.
    """
    if not calls and not text:
        return [empty]
    return _wrapped(calls or ["(no tool calls)"]) + [""] + _wrap(text or "(no prose)")


def _side_by_side(left_title: str, left: list[str], right_title: str, right: list[str]) -> str:
    width = _COLUMN_WIDTH
    rows = [
        f"  {left_title:<{width}} | {right_title}",
        f"  {'-' * width} | {'-' * width}",
    ]
    rows.extend(
        f"  {line:<{width}} | {other}"
        for line, other in itertools.zip_longest(left, right, fillvalue="")
    )
    return "\n".join(rows)
