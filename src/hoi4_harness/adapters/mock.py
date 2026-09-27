"""A deterministic fake game.

This is not a HOI4 simulator and does not try to be. It is a cheap, seeded stand-in
with the same *shape* as the real thing -- a clock, resources that accrue, queues
that drain, events that fire -- so the loop, the policy layer, the budget guard and
the eval runner can all be exercised without launching Paradox software.

It always begins as the same neutral 1936 Sweden unless handed a
``ScenarioStart``: a scenario's description of its own opening position (at war,
with fronts, starved of resources, sabotaged). The mock interprets any described
start; it never special-cases which scenario is speaking.

Every test in this repo runs against it.
"""

from __future__ import annotations

import copy
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
    ScenarioStart,
    WarChange,
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
            "set_trade",
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

    def __init__(
        self,
        seed: int = 1936,
        country: str = "SWE",
        start: str = "1936-01-01",
        start_state: ScenarioStart | None = None,
    ):
        """``start`` is the date; ``start_state`` is everything else a scenario
        wants different about the opening position. Without one, this is the
        same neutral 1936 Sweden it has always been."""
        self.rng = random.Random(seed)
        self.date = dt.date.fromisoformat(start)
        self.templates: dict[str, dict] = {
            "Infantry": {"battalions": 9, "width": 18.0},
        }
        self.state = GameState(
            date=start,
            country=country,
            country_name={
                "SWE": "Sweden",
                "USA": "United States",
                "GER": "Germany",
                "FIN": "Finland",
            }.get(country, country),
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
            resources={"steel": 40, "aluminium": 25, "oil": 12, "rubber": 20,
                       "tungsten": 10, "chromium": 12},
        )
        self.scripted_events = dict(SCRIPTED_EVENTS)
        self._pending: list[GameEvent] = []
        self._advisor_pp_bonus = 0.0
        self._trades: dict[str, int] = {}
        self._attrition: dict[str, float] = {}
        self._losing_days: dict[str, int] = {}
        self._timeline: dict[str, list[WarChange]] = {}
        if start_state is not None:
            self._apply_start(start_state)

    def _apply_start(self, described: ScenarioStart) -> None:
        """Lay a scenario's described start over the default one.

        Scalars replace, dicts merge (a start states only what differs), lists
        replace wholesale. The scenario owns the picture; nothing here knows or
        cares which scenario is speaking.
        """
        s = self.state
        for name in ("stability", "war_support", "political_power", "manpower",
                     "civilian_factories", "military_factories", "dockyards",
                     "fuel", "convoys"):
            value = getattr(described, name)
            if value is not None:
                setattr(s, name, value)
        if described.resources:
            s.resources.update(described.resources)
        if described.stockpiles:
            s.stockpiles.update(described.stockpiles)
        for name in ("research", "production", "construction", "divisions",
                     "fronts", "wars"):
            value = getattr(described, name)
            if value is not None:
                # Copied, not shared: the scenario object outlives the adapter,
                # and a run that mutates its divisions must not poison the next
                # run of the same scenario.
                setattr(s, name, copy.deepcopy(value))
        if described.events:
            self.scripted_events.update(described.events)
        for change in described.timeline or []:
            self._timeline.setdefault(change.date, []).append(copy.deepcopy(change))
        # A front's friendly count is derived from where the divisions stand,
        # as every tick does; doing it now means day zero agrees with day one
        # instead of reporting whatever number the description guessed.
        for front in s.fronts:
            front.divisions_friendly = sum(g.count for g in s.divisions if g.location == front.name)

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
                s.completed_focuses.append(s.national_focus)
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

        # Crudest possible shortage model: military lines run at half output
        # while the country has no rubber. Rubber stands in for every resource a
        # country must trade for or synthesise; half output is the entire
        # theory. Oil is displayed but inert -- the mock has no fuel consumption
        # to starve. ``resources`` is a daily-income view, like the game's
        # resource bar, so nothing accrues into it here.
        shortage = 0.5 if s.resources.get("rubber", 0) <= 0 else 1.0
        for line in s.production:
            line.efficiency = min(1.0, line.efficiency + 0.004)
            line.output_per_day = round(
                line.factories * 4.5 * (0.35 + 0.65 * line.efficiency) * shortage, 2
            )
            got = int(line.output_per_day)
            s.stockpiles[line.equipment] = s.stockpiles.get(line.equipment, 0) + got

        for change in self._timeline.get(iso, []):
            self._apply_change(change)

        if s.fronts:
            self._tick_fronts()

        if iso in self.scripted_events:
            kind, text, severity = self.scripted_events[iso]
            self._emit(kind, text, severity)

    def _tick_fronts(self) -> None:
        """The mock's entire theory of land combat, and deliberately no more.

        Armies delegated to the game AI on a defensive posture are shifted by
        that AI onto the worst-pressed front; a front holds while it is not
        outnumbered worse than two to one; a front that is losing ground bleeds
        about a division every ten days. Nothing here resembles real combat: it
        exists so a defensive scenario can ask whether the agent reinforced a
        collapsing front, and score the answer.
        """
        s = self.state
        defended = [f for f in s.fronts if self.posture_on(f.name) == "defensive"]
        # An invade directive against an enemy the country is already fighting
        # pulls the reserve to that front ahead of any posture -- the one way
        # the mock lets a directive be seen to work, so that the directive
        # feedback line has something to observe. Against a country it is not
        # at war with, it does nothing, as in the game.
        invading = {d.split()[1] for d in s.ai_directives if d.startswith("invade ")}
        targeted = [f for f in s.fronts if f.enemy in invading]
        if s.delegated_armies and (targeted or defended):
            pool = targeted or defended
            worst = max(pool, key=lambda f: f.divisions_enemy - f.divisions_friendly)
            for group in s.divisions:
                if group.location in ("home", "unassigned"):
                    group.location = worst.name
        for front in s.fronts:
            front.divisions_friendly = sum(
                g.count for g in s.divisions if g.location == front.name
            )
            posture = self.posture_on(front.name) if s.delegated_armies else None
            front.stance = "offensive" if posture == "offensive" else "hold"
            holds = front.divisions_friendly * 2 >= front.divisions_enemy
            if not holds:
                front.pressure = "losing_ground"
            elif posture == "offensive" and front.divisions_friendly * 2 >= front.divisions_enemy * 3:
                front.pressure = "advancing"
            else:
                front.pressure = "stable"
            self._tick_front_flags(front)
            if not holds:
                self._attrition[front.name] = self._attrition.get(front.name, 0.0) + 0.1
                if self._attrition[front.name] >= 1.0 and self._bleed_one_division(front.name):
                    self._attrition[front.name] -= 1.0

    def _tick_front_flags(self, front) -> None:
        """Supply, pockets and the capital, at the same level of crudeness.

        Flags, not mechanics: none of these costs a division. They exist so the
        wake rules and the brief have the wartime signals a real front throws
        off. Supply that was never observed (None) stays unobserved.
        """
        if front.supply is not None:
            step = {"advancing": -0.02, "losing_ground": -0.01}.get(front.pressure, 0.01)
            front.supply = round(min(1.0, max(0.2, front.supply + step)), 2)
        outnumbered = front.divisions_enemy >= 3 * max(1, front.divisions_friendly)
        pocketed = front.pressure == "losing_ground" and outnumbered and front.divisions_friendly
        front.pocket_divisions = max(1, front.divisions_friendly // 3) if pocketed else 0
        if front.pressure == "losing_ground":
            self._losing_days[front.name] = self._losing_days.get(front.name, 0) + 1
        else:
            self._losing_days[front.name] = 0
        front.threatens_capital = self._losing_days[front.name] >= 45

    def _apply_change(self, change: WarChange) -> None:
        """One dated step of a scripted war. Only what the agent does not control."""
        s = self.state
        for war in change.open_wars:
            if not any(w.against == war.against for w in s.wars):
                s.wars.append(copy.deepcopy(war))
        for tag in change.end_wars:
            s.wars = [w for w in s.wars if w.against != tag]
        for front in change.open_fronts:
            if not any(f.name == front.name for f in s.fronts):
                s.fronts.append(copy.deepcopy(front))
                # A new front's friendly count is how many divisions the war
                # plan sends there from the reserve, as far as the reserve goes.
                self._send_from_home(front.name, front.divisions_friendly)
        if change.close_fronts:
            closing = set(change.close_fronts)
            s.fronts = [f for f in s.fronts if f.name not in closing]
            # The troops on a closed front come home; nobody is left standing
            # on a line that no longer exists.
            for group in s.divisions:
                if group.location in closing:
                    group.location = "home"
            self._merge_groups()
            s.theater_postures = {k: v for k, v in s.theater_postures.items() if k not in closing}
        for name, count in change.enemy_divisions.items():
            for front in s.fronts:
                if front.name == name:
                    front.divisions_enemy = count
        for name, supply in change.supply.items():
            for front in s.fronts:
                if front.name == name:
                    front.supply = supply
        for tag in change.allies_fall:
            for war in s.wars:
                war.allies = [a for a in war.allies if a != tag]
            self._emit("ally_capitulated", f"Our ally {tag} has capitulated.", "notable")
        if change.reinforcements:
            home = next((g for g in s.divisions if g.location == "home"), None)
            if home is None:
                home = DivisionGroup(template="Infantry", count=0, location="home")
                s.divisions.append(home)
            home.count += change.reinforcements
        if change.event:
            self._emit(*change.event)

    def _merge_groups(self) -> None:
        """One group per (template, location), so the brief does not list 'home' twice."""
        merged: dict[tuple[str, str], DivisionGroup] = {}
        for group in self.state.divisions:
            key = (group.template, group.location)
            if key in merged:
                merged[key].count += group.count
            else:
                merged[key] = group
        self.state.divisions = [g for g in merged.values() if g.count > 0]

    def _send_from_home(self, location: str, count: int) -> None:
        for group in list(self.state.divisions):
            if count <= 0:
                break
            if group.location != "home" or group.count <= 0:
                continue
            moved = min(count, group.count)
            group.count -= moved
            count -= moved
            target = next((g for g in self.state.divisions
                           if g.location == location and g.template == group.template), None)
            if target is None:
                self.state.divisions.append(
                    DivisionGroup(template=group.template, count=moved, location=location)
                )
            else:
                target.count += moved
        self.state.divisions = [g for g in self.state.divisions if g.count > 0]

    def posture_on(self, front_name: str) -> str | None:
        """The posture that governs one front: its theater's, else the global one."""
        return self.state.theater_postures.get(front_name, self.state.posture)

    def _bleed_one_division(self, front_name: str) -> bool:
        """Destroy one division on a front that is giving ground. False: nobody left."""
        groups = [g for g in self.state.divisions if g.location == front_name and g.count > 0]
        if not groups:
            return False
        victim = max(groups, key=lambda g: g.count)
        victim.count -= 1
        self.state.divisions = [g for g in self.state.divisions if g.count > 0]
        self._emit("front_casualties", f"A division was destroyed on the {front_name} front.",
                   "notable")
        return True

    def _finish_building(self, item: ConstructionItem) -> None:
        s = self.state
        if item.building == "civilian_factory":
            s.civilian_factories += 1
        elif item.building == "military_factory":
            s.military_factories += 1
        elif item.building == "dockyard":
            s.dockyards += 1
        elif item.building == "synthetic_refinery":
            # A finished refinery yields rubber the country cannot otherwise
            # make; that is the whole of the mock's synthetics.
            s.resources["rubber"] = s.resources.get("rubber", 0) + 12
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

    def _do_set_trade(self, call: ActionCall) -> ActionResult:
        """The crudest possible trade: each civilian factory committed buys a
        flat 4 units of the resource a day and is lost to construction while the
        trade runs. Re-trading a resource is the only way to change or cancel
        one -- the catalog has no un-trade -- and there is no world market to
        run out of."""
        resource = call.arguments["resource"]
        factories = int(call.arguments["factories"])
        committed = self._trades.get(resource, 0)
        free = self.state.civilian_factories + committed
        if factories > free:
            return self._fail(
                call,
                f"Only {free} civilian factories are free to trade; "
                f"{committed} already buy {resource}.",
            )
        self.state.civilian_factories = free - factories
        self._trades[resource] = factories
        s = self.state
        s.resources[resource] = s.resources.get(resource, 0) + 4 * (factories - committed)
        return self._ok(
            call,
            f"{factories} civilian factories now buy {resource} from "
            f"{call.arguments['from_country']}.",
            resource=resource,
            factories=factories,
        )

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
        location = call.arguments.get("location", "home")
        if template not in self.templates:
            return self._fail(call, f"No template named '{template}'.", "invalid_args")
        need = count * self.templates[template]["battalions"] * 100
        have = self.state.stockpiles.get("infantry_equipment_1", 0)
        if have < need:
            return self._fail(call, f"Need {need} equipment, have {have}.")
        self.state.stockpiles["infantry_equipment_1"] = have - need
        group = next(
            (g for g in self.state.divisions if g.template == template and g.location == location),
            None,
        )
        if group:
            group.count += count
        else:
            self.state.divisions.append(
                DivisionGroup(template=template, count=count, location=location)
            )
        return self._ok(call, f"Deploying {count}x {template} to {location}.", equipment_spent=need)

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
        posture = call.arguments["posture"]
        theater = call.arguments.get("theater")
        if theater:
            if not any(front.name == theater for front in self.state.fronts):
                known = ", ".join(f.name for f in self.state.fronts) or "none"
                return self._fail(
                    call, f"No front named '{theater}'. Fronts: {known}.", "invalid_args"
                )
            self.state.theater_postures[theater] = posture
            return self._ok(call, f"Posture on {theater} set to {posture}.", theater=theater)
        self.state.posture = posture
        return self._ok(call, f"Posture set to {posture} on every front without its own.")

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
        self.state.theater_postures = {}
        return self._ok(call, f"Cleared {dropped} directive(s).")
