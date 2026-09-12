"""The human-in-the-loop confirmation gate.

``HOI4Env.confirm_hook`` is the structural seam: an irreversible action passes
through it when the run is live and the gate is on (see
:meth:`hoi4_harness.env.HOI4Env.act`). This module is the implementation the
harness ships -- a terminal prompt -- and the request it is handed.

Two decisions worth stating:

* **A denial is data, not an error.** Every decision, approval or veto, is
  written to the transcript, because a run where a human blocked three invasions
  is not the same run as one that never tried. The transcript is what later
  analysis reads, so the denials have to be in it.
* **Off unless asked for.** Unattended runs never install the hook (``play
  --confirm`` does), so nothing here can block a run nobody is watching. Once it
  is installed, an exhausted stdin is a *no*: when nobody answers, the safe side
  of an irreversible action is not to take it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TextIO

from .actions import catalog
from .types import ActionCall

if TYPE_CHECKING:
    from .agent.loop import Transcript

YES = {"y", "yes"}
NO = {"n", "no"}
ALWAYS = {"a", "always"}


@dataclass
class Confirmation:
    """One gated action, and everything a person needs to judge it.

    Built by the env at the moment of the gate, because the env is where the
    action is about to run. ``date`` and ``turn`` come from the loop, which knows
    the game's clock; the env does not, and must not read it -- on a screen
    adapter a state read is a vision-model call.
    """

    call: ActionCall
    date: str = ""
    turn: int = 0

    @property
    def rationale(self) -> str | None:
        """What the model said it was doing, in its own words.

        Carried on the call: the loop fills it from the response text, and a
        reflex action writes its own. Nothing here invents one.
        """
        return self.call.rationale

    @property
    def scope(self) -> str:
        """What an "always" answer covers: the action and the mode it is used in.

        The action name alone is too coarse. ``diplomacy`` bundles reversible
        gestures and war declarations under one verb, so saying "always" to a
        lend-lease must not pre-approve every later war. The mode is read from
        the catalog's own enum arguments rather than special-cased here, so this
        stays right as the catalog grows.
        """
        spec = catalog.get(self.call.name)
        properties = (spec.parameters.get("properties") if spec else None) or {}
        modes = [
            str(self.call.arguments[name])
            for name, schema in properties.items()
            if "enum" in schema and name in self.call.arguments
        ]
        return ":".join([self.call.name, *modes])


@dataclass
class TerminalConfirmer:
    """Ask the operator, in the terminal, before an irreversible action runs.

    Everything is written to stderr and the question is read with an empty
    prompt string, both so that ``play``'s stdout stays the final JSON --
    ``input("...")`` would print the prompt to stdout. ``input_fn`` is injectable
    so the tests can answer without a tty, the same shape ``calibration`` uses
    to wait for the operator.
    """

    transcript: Transcript
    input_fn: Callable[[str], str] = input
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    #: Scopes answered "always" this session. Deliberately not persisted: a
    #: resumed run asks again, because the person who answered may be gone.
    remembered: set[str] = field(default_factory=set)

    def __call__(self, request: Confirmation) -> bool:
        if request.scope in self.remembered:
            # Answering "always" is a promise not to be asked again. It is still
            # a decision, and it still lands in the transcript.
            self._record(request, approved=True, answer="always", prompted=False)
            return True

        answer = self._ask(request)
        approved = answer in {"yes", "always"}
        if answer == "always":
            self.remembered.add(request.scope)
        self._record(request, approved=approved, answer=answer, prompted=True)
        return approved

    def _ask(self, request: Confirmation) -> str:
        call = request.call
        args = ", ".join(
            f"{key}={json.dumps(value, default=str)}" for key, value in call.arguments.items()
        )
        print(
            f"\n[confirm {request.date or 'date unknown'} | turn {request.turn}] "
            f"'{call.name}' is irreversible",
            file=self.stream,
            flush=True,
        )
        print(f"  do:  {call.name}({args})", file=self.stream, flush=True)
        rationale = " ".join((request.rationale or "").split())
        print(
            f"  why: {rationale or '(the model gave no rationale)'}",
            file=self.stream,
            flush=True,
        )
        print(
            f"  yes / no / always (always = every '{request.scope}' this session) [no]",
            file=self.stream,
            flush=True,
        )
        try:
            raw = self.input_fn("").strip().lower()
        except EOFError:
            # Nobody is there. Everything from here on is the safe answer.
            print("  no input: reading it as no", file=self.stream, flush=True)
            return "no"
        if raw in YES:
            return "yes"
        if raw in ALWAYS:
            return "always"
        if raw in NO:
            return "no"
        print(f"  {raw!r} is not one of them: reading it as no", file=self.stream, flush=True)
        return "no"

    def _record(self, request: Confirmation, *, approved: bool, answer: str, prompted: bool) -> None:
        self.transcript.write(
            "confirmation",
            date=request.date,
            turn=request.turn,
            call_id=request.call.call_id,
            name=request.call.name,
            arguments=request.call.arguments,
            rationale=request.rationale,
            scope=request.scope,
            decision="approved" if approved else "denied",
            answer=answer,
            prompted=prompted,
        )
