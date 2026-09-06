"""A deterministic fake game.

This is not a HOI4 simulator and does not try to be. It is a cheap, seeded stand-in
with the same *shape* as the real thing -- a clock, resources that accrue, queues
that drain, events that fire -- so the loop, the policy layer, the budget guard and
the eval runner can all be exercised without launching Paradox software.

Every test in this repo runs against it.
"""

from __future__ import annotations

import datetime as dt
import random

from ..types import (
    ActionCall,
    ActionResult,
    ConstructionItem,
    DivisionGroup,
    GameEvent,
    GameState,
    ProductionLine,
    ResearchSlot,
)
from .base import AdapterInfo, GameAdapter

BUILD_DAYS = {
    "civilian_factory": 120,
    "military_factory": 100,
    "dockyard": 90,
    "infrastructure": 60,
    "air_base": 45,
    "anti_air": 30,
    "radar": 60,
    "fortification": 40,
    "synthetic_refinery": 130,
}

# Scripted history, so a run is comparable between agents.
SCRIPTED_EVENTS = {
    "1936-03-07": ("rhineland", "Germany remilitarises the Rhineland.", "notable"),
    "1936-07-17": ("spain", "Civil war breaks out in Spain.", "notable"),
    "1938-03-12": ("anschluss", "Germany annexes Austria.", "notable"),
    "1939-09-01": ("war", "Germany invades Poland. The war has begun.", "critical"),
}


class MockAdapter(GameAdapter):
    supported_actions = frozenset(
        {
            "set_national_focus",
            "start_research",
            "queue_construction",
            "set_production",
            "design_division_template",
            "deploy_divisions",
            "hire_advisor",
            "set_game_speed",
            "advance_time",
            "note",
            "delegate_army_to_ai",
            "set_ai_posture",
            "set_ai_directive",
            "clear_ai_directives",
        }
    )

    def __init__(self, seed: int = 1936, country: str = "SWE", start: str = "1936-01-01"):
        self.rng = random.Random(seed)
        self.date = dt.date.fromisoformat(start)
        self.templates: dict[str, dict] = {
            "Infantry": {"battalions": 9, "width": 18.0},
        }
        self.state = GameState(
            date=start,
            country=country,
            country_name={"SWE": "Sweden", "USA": "United States", "GER": "Germany"}.get(
                country, country
            ),
            political_power=25.0,
            stability=0.62,
            war_support=0.18,
            manpower=180_000,
            civilian_factories=12,
            military_factories=5,
            dockyards=4,
            fuel=1200.0,
            convoys=60,
            national_focus=None,
            ideology="neutrality",
            research=[ResearchSlot(index=i) for i in range(3)],
            production=[ProductionLine(equipment="infantry_equipment_1", factories=5)],
            construction=[],
            stockpiles={"infantry_equipment_1": 4200, "support_equipment": 300},
            divisions=[DivisionGroup(template="Infantry", count=12, location="home")],
        )
        self._pending: list[GameEvent] = []
        self._advisor_pp_bonus = 0.0

    # --- adapter contract ----------------------------------------------------

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="mock",
            readable=True,
            writable=True,
            clock_control=True,
            notes="Deterministic stand-in. Seeded; no HOI4 install required.",
        )

    def read_state(self) -> GameState:
        self.state.date = self.date.isoformat()
        self.state.events = list(self._pending)
        return self.state

    def apply(self, call: ActionCall) -> ActionResult:
        if call.name not in self.supported_actions:
            return self.unsupported(call)
        handler = getattr(self, f"_do_{call.name}")
        return handler(call)

    def pause(self) -> None:
        self.state.speed = 0

    def resume(self, speed: int = 3) -> None:
        self.state.speed = max(1, min(5, speed))

    def advance(self, days: int) -> GameState:
        """Tick forward, stopping early on a critical event."""
        self._pending = []
        for _ in range(max(0, days)):
            self.date += dt.timedelta(days=1)
            self._tick_one_day()
            if any(e.severity == "critical" for e in self._pending):
                break
        self.pause()
        return self.read_state()

    # --- simulation ----------------------------------------------------------

    def _tick_one_day(self) -> None:
        s = self.state
        iso = self.date.isoformat()

        s.political_power = min(999.0, s.political_power + 1.0 + self._advisor_pp_bonus)
        s.manpower += 120 + 10 * s.civilian_factories

        if s.focus_days_remaining is not None:
            s.focus_days_remaining -= 1
            if s.focus_days_remaining <= 0:
                self._emit("focus_complete", f"National focus complete: {s.national_focus}", "notable")
                s.national_focus, s.focus_days_remaining = None, None

        for slot in s.research:
            if slot.days_remaining is not None:
                slot.days_remaining -= 1
                if slot.days_remaining <= 0:
                    self._emit("research_done", f"Research complete: {slot.technology}", "notable")
                    slot.technology, slot.days_remaining = None, None

        if s.construction:
            item = s.construction[0]
            speed = max(1, s.civilian_factories - len(s.construction) + 1)
            item.progress = min(1.0, item.progress + speed / (BUILD_DAYS.get(item.building, 100) * 5))
            if item.days_remaining is not None:
                item.days_remaining = max(0, item.days_remaining - 1)
            if item.progress >= 1.0:
                self._finish_building(item)
                s.construction.pop(0)

        for line in s.production:
            line.efficiency = min(1.0, line.efficiency + 0.004)
            line.output_per_day = round(line.factories * 4.5 * (0.35 + 0.65 * line.efficiency), 2)
            got = int(line.output_per_day)
            s.stockpiles[line.equipment] = s.stockpiles.get(line.equipment, 0) + got

        if iso in SCRIPTED_EVENTS:
            kind, text, severity = SCRIPTED_EVENTS[iso]
            self._emit(kind, text, severity)

    def _finish_building(self, item: ConstructionItem) -> None:
        s = self.state
        if item.building == "civilian_factory":
            s.civilian_factories += 1
        elif item.building == "military_factory":
            s.military_factories += 1
        elif item.building == "dockyard":
            s.dockyards += 1
        self._emit("construction_done", f"{item.building} finished in {item.state}", "info")

    def _emit(self, kind: str, text: str, severity: str = "info") -> None:
        self._pending.append(
            GameEvent(kind=kind, text=text, severity=severity, date=self.date.isoformat())
        )

    # --- action handlers -----------------------------------------------------

    def _ok(self, call: ActionCall, message: str, **changed) -> ActionResult:
        return ActionResult(
            ok=True, action=call.name, call_id=call.call_id, message=message, changed=changed
        )

    def _fail(self, call: ActionCall, message: str, kind: str = "rejected") -> ActionResult:
        return ActionResult(
            ok=False, action=call.name, call_id=call.call_id, message=message, error_kind=kind
        )

    def _do_set_national_focus(self, call: ActionCall) -> ActionResult:
        focus = call.arguments["focus_id"]
        if self.state.national_focus:
            return self._fail(call, f"'{self.state.national_focus}' is already in progress.")
        days = 35 + 7 * (len(focus) % 4)
        self.state.national_focus = focus
        self.state.focus_days_remaining = days
        return self._ok(call, f"Started focus '{focus}' ({days} days).", focus=focus, days=days)

    def _do_start_research(self, call: ActionCall) -> ActionResult:
        tech = call.arguments["technology"]
        free = [s for s in self.state.research if s.technology is None]
        if not free:
            return self._fail(call, "No free research slot.")
        slot = free[0]
        slot.technology = tech
        slot.days_remaining = 90 + self.rng.randint(0, 60)
        return self._ok(call, f"Researching '{tech}' in slot {slot.index}.", slot=slot.index)

    def _do_queue_construction(self, call: ActionCall) -> ActionResult:
        building = call.arguments["building"]
        state_name = call.arguments["state"]
        count = int(call.arguments.get("count", 1))
        if building not in BUILD_DAYS:
            return self._fail(call, f"Unknown building '{building}'.", "invalid_args")
        for _ in range(count):
            self.state.construction.append(
                ConstructionItem(
                    building=building, state=state_name, days_remaining=BUILD_DAYS[building]
                )
            )
        return self._ok(
            call,
            f"Queued {count}x {building} in {state_name}.",
            queue_length=len(self.state.construction),
        )

    def _do_set_production(self, call: ActionCall) -> ActionResult:
        equipment = call.arguments["equipment"]
        factories = int(call.arguments["factories"])
        assigned = sum(line.factories for line in self.state.production)
        existing = next((x for x in self.state.production if x.equipment == equipment), None)
        current = existing.factories if existing else 0
        if assigned - current + factories > self.state.military_factories:
            return self._fail(
                call,
                f"Only {self.state.military_factories} military factories; "
                f"{assigned - current} already assigned.",
            )
        if existing:
            existing.factories = factories
        else:
            self.state.production.append(ProductionLine(equipment=equipment, factories=factories))
        return self._ok(call, f"{equipment}: {factories} factories.", equipment=equipment)

    def _do_design_division_template(self, call: ActionCall) -> ActionResult:
        name = call.arguments["name"]
        battalions = call.arguments.get("battalions", [])
        total = sum(int(b.get("count", 0)) for b in battalions)
        if total == 0:
            return self._fail(call, "A template needs at least one battalion.", "invalid_args")
        self.templates[name] = {"battalions": total, "width": round(total * 2.0, 1)}
        return self._ok(call, f"Template '{name}' saved ({total} battalions).", template=name)

    def _do_deploy_divisions(self, call: ActionCall) -> ActionResult:
        template = call.arguments["template"]
        count = int(call.arguments["count"])
        if template not in self.templates:
            return self._fail(call, f"No template named '{template}'.", "invalid_args")
        need = count * self.templates[template]["battalions"] * 100
        have = self.state.stockpiles.get("infantry_equipment_1", 0)
        if have < need:
            return self._fail(call, f"Need {need} equipment, have {have}.")
        self.state.stockpiles["infantry_equipment_1"] = have - need
        group = next(
            (g for g in self.state.divisions if g.template == template and g.location == "home"),
            None,
        )
        if group:
            group.count += count
        else:
            self.state.divisions.append(DivisionGroup(template=template, count=count))
        return self._ok(call, f"Deploying {count}x {template}.", equipment_spent=need)

    def _do_hire_advisor(self, call: ActionCall) -> ActionResult:
        cost = 150.0
        if self.state.political_power < cost:
            return self._fail(call, f"Needs {cost} PP, have {self.state.political_power:.0f}.")
        self.state.political_power -= cost
        self._advisor_pp_bonus += 0.15
        return self._ok(call, f"Hired {call.arguments['advisor_id']}.", pp_left=self.state.political_power)

    def _do_set_game_speed(self, call: ActionCall) -> ActionResult:
        speed = int(call.arguments["speed"])
        self.state.speed = max(0, min(5, speed))
        return self._ok(call, f"Speed set to {self.state.speed}.")

    def _do_advance_time(self, call: ActionCall) -> ActionResult:
        days = int(call.arguments.get("days", 7))
        self.advance(days)
        return self._ok(call, f"Advanced to {self.date.isoformat()}.", date=self.date.isoformat())

    def _do_note(self, call: ActionCall) -> ActionResult:
        return self._ok(call, "Noted.", text=call.arguments.get("text", "")[:200])

    # --- hybrid control ------------------------------------------------------
    # A crude stand-in: delegated armies are simply recorded, and a defensive
    # posture makes the mock's scripted fronts hold. It is enough to exercise the
    # control split end to end; it says nothing about whether the real AI would
    # do better.

    def _do_delegate_army_to_ai(self, call: ActionCall) -> ActionResult:
        army = call.arguments["army"]
        delegate = bool(call.arguments["delegate"])
        armies = ["all"] if army == "all" else [army]
        if delegate:
            for name in armies:
                if name not in self.state.delegated_armies:
                    self.state.delegated_armies.append(name)
        else:
            self.state.delegated_armies = [
                name for name in self.state.delegated_armies if name not in armies
            ]
        verb = "delegated to" if delegate else "recalled from"
        return self._ok(
            call,
            f"{army} {verb} the game AI.",
            delegated=len(self.state.delegated_armies),
        )

    def _do_set_ai_posture(self, call: ActionCall) -> ActionResult:
        if not self.state.delegated_armies:
            return self._fail(call, "No armies are delegated, so posture does nothing yet.")
        self.state.posture = call.arguments["posture"]
        return self._ok(call, f"Posture set to {self.state.posture}.")

    def _do_set_ai_directive(self, call: ActionCall) -> ActionResult:
        directive = call.arguments["directive"]
        target = call.arguments["target"]
        weight = int(call.arguments.get("weight", 100))
        entry = f"{directive} {target} ({weight})"
        self.state.ai_directives = [
            d for d in self.state.ai_directives if not d.startswith(f"{directive} {target} ")
        ]
        self.state.ai_directives.append(entry)
        return self._ok(call, f"Directive standing: {entry}.", directives=len(self.state.ai_directives))

    def _do_clear_ai_directives(self, call: ActionCall) -> ActionResult:
        dropped = len(self.state.ai_directives)
        self.state.ai_directives = []
        self.state.posture = None
        return self._ok(call, f"Cleared {dropped} directive(s).")
