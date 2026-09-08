"""Prompt construction.

The system prompt is assembled from three layers, in this order:

1. **Mechanics** (fixed) -- what the tools are, how a turn works, what the reports
   mean. Without this nothing functions, so it is not configurable.
2. **Guidance** (swappable) -- how to play, from nothing at all to a prescriptive
   checklist. See :mod:`hoi4_harness.guidance`.
3. **Extra** (optional) -- whatever the operator appends, or a complete
   replacement via ``system_prompt_path``.

The split is not cosmetic. How much coaching a model needs is the interesting
experimental variable in this project, and it belongs in a file you can swap,
not welded into the harness. The whole assembled prompt is still byte-stable
across a run, so prefix caching works regardless of which layers are in play.
"""

from __future__ import annotations

from pathlib import Path

from ..guidance import load as load_guidance

MECHANICS = """You are playing a single-player campaign of Hearts of Iron IV as the \
country named in each situation report. You act through tools; the harness \
executes them and reports back what actually happened.

How the game reaches you:
- You are woken only when something needs deciding. Between wakes the harness \
runs the clock and handles routine upkeep.
- A report may be a full situation brief or a list of changes since the last one. \
Treat a change list as an update to what you already knew.
- Anything marked "unknown" is not observable through the current bridge. Do not \
reason about it as if it were zero.

How to act:
- Take the smallest set of actions that moves the plan forward, then call \
advance_time. Doing nothing for a week is a legitimate and cheap move.
- One thing at a time gets built, researched, and focused on. Check the report \
before assuming a slot is free.
- Use `note` to record intent that should outlive this turn: what you are \
building toward, and what would change your mind. Your notes come back to you.
- If an action is rejected, the reason is in the result. Fix the argument or \
choose a different action; do not repeat the same call."""

ADVISOR = """Advisor mode. You are not playing this campaign; a person is. Every tool \
call you make is shown to them as a recommendation, and none of them are \
executed -- the harness cannot act, so the game will never change because you \
called something. That is expected, not an error. Use the tools exactly as you \
would if you were playing: concrete calls are the advice. `note` still writes \
your journal. Call advance_time to end the turn, as usual."""


def build_system(
    guidance: str | Path | None = "doctrine",
    extra: str = "",
    system_prompt_path: str | Path | None = None,
    operational_control: str = "llm",
    advisor: bool = False,
) -> str:
    """Assemble the system prompt.

    ``system_prompt_path`` replaces everything, mechanics included -- total
    control, and entirely your problem if the model then cannot use the tools.
    """
    if system_prompt_path:
        return Path(system_prompt_path).read_text(encoding="utf-8").strip()

    from .hybrid import describe

    parts = [MECHANICS, describe(operational_control)]
    if advisor:
        # Sits with the mechanics, before any guidance: it corrects the fiction
        # that the harness executes what the model calls.
        parts.append(ADVISOR)
    text = load_guidance(guidance)
    if text:
        parts.append(text)
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)


def turn_prompt(
    brief: str,
    objective: str,
    memory_block: str,
    wake_reason: str,
    turn: int,
    actions_left: int,
) -> str:
    """The volatile half: everything that changes turn to turn."""
    parts = [f"Turn {turn}. You were woken because: {wake_reason}."]
    if objective:
        parts.append(f"Standing objective: {objective}")
    if memory_block:
        parts.append(memory_block)
    parts.append(brief)
    parts.append(
        f"Take up to {actions_left} actions, then call advance_time to hand the clock back."
    )
    return "\n\n".join(parts)


# Backwards-compatible default, used when nothing overrides it.
SYSTEM = build_system("doctrine")
