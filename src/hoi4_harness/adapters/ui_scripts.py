"""UI scripts: how the input driver performs an action, and how it knows it worked.

A blind click reported as success is the worst failure in this design: the agent
plans on top of a change that never happened. So every scripted action is two
halves, kept apart on purpose:

* **the click path** -- press this hotkey, click that calibrated target, type
  the focus id into the search box. It depends on the game version, the UI
  scale and the operator's layout, so, like coordinates, it lives in a file
  the operator records against their own game (``ui_scripts.json``), never in
  code. Steps may carry ``{placeholders}`` filled from the action's arguments.
* **the verification** -- what must be true of the game state afterwards. That
  does not depend on the UI at all (a started focus is running; a queued
  building lengthens the queue), so it lives here, in code, with tests.

After the steps run, the driver re-reads the game through its reader and polls
until the verification passes or a timeout expires. It reports failure rather
than success when the UI was in an unexpected state -- and reports
*unverified*, never success, when the reader cannot see the field that would
prove it (the log-tail bridge does not emit the focus yet, for one).

File format::

    {
      "schema": 1,
      "scripts": {
        "set_national_focus": [
          {"press": "f"},
          {"click": "focus.search"},
          {"type": "{focus_id}"},
          {"press": "enter"},
          {"click": "focus.first_result"},
          {"press": "escape"}
        ]
      }
    }
"""

from __future__ import annotations

import json
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..types import ActionCall, GameState

SCHEMA_VERSION = 1
DEFAULT_FILENAME = "ui_scripts.json"
STEP_KINDS = ("press", "click", "type", "wait")


@dataclass(frozen=True)
class Step:
    kind: str       # press | click | type | wait
    value: str

    def fill(self, arguments: dict) -> Step:
        """Substitute ``{placeholders}`` from the action's arguments."""
        if self.kind == "wait":
            return self
        names = {name for _, name, _, _ in string.Formatter().parse(self.value) if name}
        missing = names - set(arguments)
        if missing:
            raise KeyError(f"step {self.kind}={self.value!r} needs argument(s) {sorted(missing)}")
        return Step(self.kind, self.value.format(**{k: str(v) for k, v in arguments.items()}))

    def describe(self) -> str:
        return f"{self.kind} {self.value}"


def parse_steps(raw: list[dict]) -> list[Step]:
    steps = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(f"step {index}: expected one of {STEP_KINDS} as a single key, got {entry!r}")
        (kind, value), = entry.items()
        if kind not in STEP_KINDS:
            raise ValueError(f"step {index}: unknown step {kind!r}; known: {', '.join(STEP_KINDS)}")
        if kind == "wait":
            float(value)
        steps.append(Step(kind, str(value)))
    return steps


def load_scripts(path: Path) -> dict[str, list[Step]]:
    """Read a scripts file, refusing one written for another schema or naming an
    action that has no verification -- a script without one would be exactly
    the blind click this module exists to prevent."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema {data.get('schema')!r}, this harness reads {SCHEMA_VERSION}")
    scripts = {}
    for action, raw in (data.get("scripts") or {}).items():
        if action not in VERIFIERS:
            raise ValueError(
                f"{path}: no verification exists for {action!r}, so it cannot be scripted. "
                f"Scriptable: {', '.join(sorted(VERIFIERS))}"
            )
        scripts[action] = parse_steps(raw)
    return scripts


def targets_in(scripts: dict[str, list[Step]]) -> list[str]:
    """Every click target the scripts need, for ``hoi4-harness calibrate``.
    Targets with placeholders are listed as written."""
    seen: dict[str, None] = {}
    for steps in scripts.values():
        for step in steps:
            if step.kind == "click":
                seen[step.value] = None
    return list(seen)


# --- verification ------------------------------------------------------------

#: The fields each verification reads. If the reader marks one unknown, the
#: action is reported unverified rather than guessed at.
NEEDS = {
    "set_national_focus": ("national_focus",),
    "start_research": ("research",),
    "queue_construction": ("construction",),
    "set_production": ("production",),
    "hire_advisor": ("political_power",),
    "delegate_army_to_ai": ("delegated_armies",),
    "set_ai_posture": ("posture",),
    "set_ai_directive": ("ai_directives",),
    "clear_ai_directives": ("ai_directives", "posture"),
}


def _verify_focus(before: GameState, after: GameState, args: dict) -> str | None:
    if after.national_focus == args["focus_id"]:
        return None
    return f"the running focus is {after.national_focus or 'none'}, not {args['focus_id']}"


def _verify_research(before: GameState, after: GameState, args: dict) -> str | None:
    if any(slot.technology == args["technology"] for slot in after.research):
        return None
    return f"{args['technology']} is not in any research slot"


def _verify_construction(before: GameState, after: GameState, args: dict) -> str | None:
    count = int(args.get("count", 1))
    added = sum(1 for item in after.construction if item.building == args["building"]) - sum(
        1 for item in before.construction if item.building == args["building"]
    )
    if added >= count:
        return None
    return f"the queue gained {max(added, 0)} {args['building']}, not {count}"


def _verify_production(before: GameState, after: GameState, args: dict) -> str | None:
    line = next((x for x in after.production if x.equipment == args["equipment"]), None)
    have = line.factories if line else 0
    if have == int(args["factories"]):
        return None
    return f"{args['equipment']} has {have} factories, not {args['factories']}"


def _verify_advisor(before: GameState, after: GameState, args: dict) -> str | None:
    # No adapter reports advisors, but hiring one always costs political power:
    # PP falling is the observable consequence. Weak, and said so.
    if after.political_power < before.political_power:
        return None
    return "political power did not fall, so no advisor was hired"


def _verify_delegation(before: GameState, after: GameState, args: dict) -> str | None:
    army, delegate = args["army"], bool(args["delegate"])
    held = army in after.delegated_armies or "all" in after.delegated_armies
    if army == "all" and not delegate:
        held = bool(after.delegated_armies)
    if held == delegate:
        return None
    return f"{army} is {'still not' if delegate else 'still'} under AI control"


def _verify_posture(before: GameState, after: GameState, args: dict) -> str | None:
    theater = args.get("theater")
    now = after.theater_postures.get(theater) if theater else after.posture
    if now == args["posture"]:
        return None
    where = f" on {theater}" if theater else ""
    return f"posture{where} is {now or 'default'}, not {args['posture']}"


def _verify_directive(before: GameState, after: GameState, args: dict) -> str | None:
    wanted = f"{args['directive']} {args['target']}"
    if any(entry == wanted or entry.startswith(wanted + " ") for entry in after.ai_directives):
        return None
    return f"no standing directive '{wanted}'"


def _verify_cleared(before: GameState, after: GameState, args: dict) -> str | None:
    if not after.ai_directives and after.posture is None:
        return None
    return f"{len(after.ai_directives)} directive(s) and posture {after.posture} still stand"


VERIFIERS: dict[str, Callable[[GameState, GameState, dict], str | None]] = {
    "set_national_focus": _verify_focus,
    "start_research": _verify_research,
    "queue_construction": _verify_construction,
    "set_production": _verify_production,
    "hire_advisor": _verify_advisor,
    "delegate_army_to_ai": _verify_delegation,
    "set_ai_posture": _verify_posture,
    "set_ai_directive": _verify_directive,
    "clear_ai_directives": _verify_cleared,
}


@dataclass
class Outcome:
    ok: bool
    kind: str | None      # None | unverified | rejected
    message: str


def verify(
    call: ActionCall,
    before: GameState,
    read_state: Callable[[], GameState],
    *,
    timeout: float = 3.0,
    poll: float = 0.25,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Poll the reader until the action's consequence shows up, or say why not."""
    after = read_state()
    needs = list(NEEDS[call.name])
    if call.name == "set_ai_posture" and call.arguments.get("theater"):
        # A theater-scoped posture is proved by the per-theater map, not the global one.
        needs.append("theater_postures")
    unseen = [name for name in needs if not after.known(name)]
    if unseen:
        return Outcome(False, "unverified", (
            f"input sent, but the reader cannot see {', '.join(unseen)}, so the change could not "
            "be confirmed. Treat it as not done until a later brief shows it."
        ))
    check = VERIFIERS[call.name]
    waited = 0.0
    problem = check(before, after, call.arguments)
    while problem is not None and waited < timeout:
        sleep(poll)
        waited += poll
        after = read_state()
        problem = check(before, after, call.arguments)
    if problem is None:
        return Outcome(True, None, "done and verified")
    return Outcome(False, "rejected", (
        f"input sent but the game did not change: {problem}. The UI was probably not in "
        "the state the script expects."
    ))
