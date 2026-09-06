"""Core data types passed between the adapter, the environment and the agent.

These are plain dataclasses on purpose: every one of them has to survive being
JSON-encoded into a prompt, appended to a transcript, and reloaded by the eval
runner.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

UNKNOWN = "unknown"


@dataclass
class ProductionLine:
    equipment: str
    factories: int
    efficiency: float = 0.0
    output_per_day: float = 0.0


@dataclass
class ResearchSlot:
    index: int
    technology: str | None = None
    days_remaining: int | None = None


@dataclass
class ConstructionItem:
    building: str
    state: str
    progress: float = 0.0
    days_remaining: int | None = None


@dataclass
class DivisionGroup:
    template: str
    count: int
    location: str = "unassigned"
    strength: float = 1.0


@dataclass
class Front:
    """One contiguous fighting line, from the agent's point of view."""

    name: str
    enemy: str
    divisions_friendly: int = 0
    divisions_enemy: int = 0
    stance: str = "hold"          # hold | offensive | retreat
    pressure: str = "stable"      # stable | advancing | losing_ground


@dataclass
class War:
    against: str
    since: str | None = None
    war_score: float = 0.0
    allies: list[str] = field(default_factory=list)


@dataclass
class GameEvent:
    """Something that happened since the last observation.

    ``severity`` is what the policy layer triages on: only ``critical`` events
    are allowed to interrupt the schedule and wake the planner early.
    """

    kind: str                     # focus_complete | research_done | war_declared | front_broken | ...
    text: str
    severity: str = "info"        # info | notable | critical
    date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GameState:
    """Normalised snapshot of everything the harness can currently see.

    An adapter that cannot see a field leaves it at its default and appends the
    field name to ``unknown_fields``; the renderer then prints "unknown" instead
    of a confident zero. Feeding an agent a fabricated 0 is worse than feeding
    it a gap.
    """

    date: str = "1936-01-01"
    country: str = "___"
    country_name: str = "Unknown"
    speed: int = 0                      # 0 = paused
    political_power: float = 0.0
    stability: float = 0.0
    war_support: float = 0.0
    manpower: int = 0
    civilian_factories: int = 0
    military_factories: int = 0
    dockyards: int = 0
    fuel: float = 0.0
    convoys: int = 0
    national_focus: str | None = None
    focus_days_remaining: int | None = None
    ideology: str = "neutrality"
    research: list[ResearchSlot] = field(default_factory=list)
    production: list[ProductionLine] = field(default_factory=list)
    construction: list[ConstructionItem] = field(default_factory=list)
    stockpiles: dict[str, int] = field(default_factory=dict)
    divisions: list[DivisionGroup] = field(default_factory=list)
    fronts: list[Front] = field(default_factory=list)
    wars: list[War] = field(default_factory=list)
    faction: str | None = None
    # --- hybrid control: what the native AI has been told to do ---------
    delegated_armies: list[str] = field(default_factory=list)
    ai_directives: list[str] = field(default_factory=list)
    posture: str | None = None
    events: list[GameEvent] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d

    def known(self, name: str) -> bool:
        return name not in self.unknown_fields


@dataclass
class Observation:
    """What the agent sees on a turn: a snapshot plus prompt-ready framing."""

    turn: int
    state: GameState
    brief: str                                   # rendered markdown, prompt-ready
    is_delta: bool = False
    legal_actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "state": self.state.to_dict(),
            "brief": self.brief,
            "is_delta": self.is_delta,
            "legal_actions": self.legal_actions,
            "notes": self.notes,
        }


@dataclass
class ActionCall:
    """A single action the agent wants to take, before validation."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResult:
    ok: bool
    message: str
    action: str = ""
    call_id: str | None = None
    changed: dict[str, Any] = field(default_factory=dict)
    error_kind: str | None = None   # invalid_action | invalid_args | rejected | unsupported | budget

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_tool_content(self) -> str:
        """Compact JSON for a tool-result block. Terse on purpose: this text is
        re-sent on every subsequent request in the turn."""
        payload: dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.changed:
            payload["changed"] = self.changed
        if self.error_kind:
            payload["error"] = self.error_kind
        return json.dumps(payload, sort_keys=True)


@dataclass
class StepResult:
    observation: Observation
    results: list[ActionResult] = field(default_factory=list)
    done: bool = False
    info: dict[str, Any] = field(default_factory=dict)
