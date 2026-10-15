"""Did a directive change anything? Report the consequence, not the intent.

``set_ai_directive`` answers "Directive standing: protect FIN (150)" -- proof the
harness recorded it, and nothing about whether the game's AI did anything
differently. An ignored directive and an obeyed one look identical, so the agent
cannot learn to stop issuing the kind that does nothing, and a directive aimed
at the wrong country (#7) passes for one that works.

So the tracker measures, at the moment a directive is raised, the things an
observer could see change if the AI took it seriously, and every brief after
that reports them against that baseline:

* divisions on fronts facing the target, and how many such fronts exist;
* whether this country is at war with the target;
* whether the target is fighting on this country's side.

These are crude and adapter-agnostic on purpose: they read only ``fronts`` and
``wars``, which every adapter either fills or marks unknown. "No observable
change after 21 days" is the line this exists to produce -- but only where the
reader can see those fields. The log-tail reader cannot see fronts or wars, and
there a baseline of zeros compared with a current state of zeros says nothing
about the AI; asserting "no change" would tell the agent its directives do
nothing when the truth is that nobody can tell. So each footprint carries what
was known when it was taken, and a part that is unknown at the baseline or now
is never compared: if nothing comparable is left, the line says the effect is
not observable with this reader.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..types import GameState


@dataclass(frozen=True)
class Footprint:
    """What an observer can see of this country's stance toward one target."""

    fronts: int
    divisions: int
    at_war: bool
    allied: bool
    #: Whether the reader could see ``state.fronts`` (fronts, divisions) and
    #: ``state.wars`` (at_war, allied) when this footprint was taken. An
    #: unknown field reads as empty, so its zeros are not a measurement.
    fronts_known: bool = True
    wars_known: bool = True

    @classmethod
    def of(cls, state: GameState, target: str) -> Footprint:
        facing = [f for f in state.fronts if f.enemy == target]
        return cls(
            fronts=len(facing),
            divisions=sum(f.divisions_friendly for f in facing),
            at_war=any(w.against == target for w in state.wars),
            allied=any(target in w.allies for w in state.wars),
            fronts_known=state.known("fronts"),
            wars_known=state.known("wars"),
        )


@dataclass
class Standing:
    directive: str
    target: str
    weight: int
    raised_on: str
    before: Footprint


class DirectiveTracker:
    """Baselines for standing directives, and the status lines diffed against them."""

    def __init__(self, max_lines: int = 4):
        self.max_lines = max_lines
        self.standing: dict[tuple[str, str], Standing] = {}

    def raised(self, directive: str, target: str, weight: int, state: GameState) -> None:
        key = (directive, target)
        if key in self.standing:
            # Re-weighting is not a new directive: keep the original baseline,
            # or re-issuing a directive that does nothing would reset its clock
            # and hide that it has been doing nothing for a month.
            self.standing[key].weight = weight
            return
        self.standing[key] = Standing(
            directive=directive, target=target, weight=weight,
            raised_on=state.date, before=Footprint.of(state, target),
        )

    def clear(self) -> None:
        self.standing.clear()

    def render(self, state: GameState) -> list[str]:
        if not self.standing:
            return []
        lines = ["Directive effect (observed, not intended):"]
        items = list(self.standing.values())
        for item in items[: self.max_lines]:
            lines.append("  " + self._line(item, state))
        if len(items) > self.max_lines:
            lines.append(f"  + {len(items) - self.max_lines} more")
        return lines

    def _line(self, item: Standing, state: GameState) -> str:
        now = Footprint.of(state, item.target)
        was = item.before
        age = _days_between(item.raised_on, state.date)
        head = f"{item.directive} {item.target}, raised {age}"
        # A part is compared only if it was known both then and now: an
        # unknown side is an empty list, not a zero worth diffing against.
        fronts_seen = was.fronts_known and now.fronts_known
        wars_seen = was.wars_known and now.wars_known
        unseen = [name for name, seen in (("fronts", fronts_seen), ("wars", wars_seen))
                  if not seen]
        changes = []
        if fronts_seen and (was.fronts, was.divisions) != (now.fronts, now.divisions):
            changes.append(
                f"divisions facing {item.target} {was.divisions} -> {now.divisions}"
                f" on {now.fronts} front(s)"
            )
        if wars_seen and was.at_war != now.at_war:
            changes.append("now at war" if now.at_war else "war ended")
        if wars_seen and was.allied != now.allied:
            changes.append("now fighting on our side" if now.allied else "no longer allied")
        caveat = f"{' and '.join(unseen)} unknown" if unseen else ""
        if not changes:
            if unseen:
                return f"{head}: effect not observable with this reader ({caveat})"
            return f"{head}: NO OBSERVABLE CHANGE"
        line = f"{head}: " + "; ".join(changes)
        return f"{line} ({caveat})" if caveat else line


def _days_between(start: str, end: str) -> str:
    try:
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    except ValueError:
        return f"on {start}"
    return "today" if days <= 0 else f"{days}d ago"
