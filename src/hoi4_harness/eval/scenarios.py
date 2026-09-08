"""Scenarios: fixed starts with checkable objectives.

A scenario is deliberately narrow. "Play Germany well" is unscoreable; "by
1937-01-01, hold 20 civilian factories with stability above 50%" is a number two
agents can be compared on. Each scenario runs against the mock adapter by
default so it costs nothing but model tokens.

A scenario that does not begin as neutral 1936 Sweden carries a
``ScenarioStart``: its own description of the opening position, which the mock
interprets -- no scenario names appear inside the adapter. Objectives are
predicates over the final ``GameState``, reading only fields the mock actually
populates, and every scenario is recorded against the reflex-only baseline
before it counts: a scenario the reflexes can pass measures nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..types import (
    DivisionGroup,
    Front,
    GameState,
    ProductionLine,
    ScenarioStart,
    War,
)


@dataclass
class Objective:
    """One checkable claim about a run.

    ``check`` reads the final state, for claims that genuinely are about where
    the run ended up. ``over`` reads every observed state instead, for claims
    that are about the whole run -- "a focus was running" is a property of a
    campaign, and testing it at one instant measures when the run happened to
    stop rather than how it was played.
    """

    name: str
    check: Callable[[GameState], bool] | None = None
    over: Callable[[list[GameState]], bool] | None = None
    weight: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if (self.check is None) == (self.over is None):
            raise ValueError(f"{self.name}: give exactly one of check= or over=")

    def holds(self, final: GameState, history: list[GameState]) -> bool:
        if self.over is not None:
            return bool(self.over(history))
        return bool(self.check(final))


def most_turns(predicate: Callable[[GameState], bool], fraction: float = 0.8):
    """An objective that has to hold for most of the run, not just at the end."""

    def over(history: list[GameState]) -> bool:
        if not history:
            return False
        held = sum(1 for state in history if predicate(state))
        return held / len(history) >= fraction

    return over


@dataclass
class Scenario:
    key: str
    title: str
    country: str
    start: str
    #: The run ends when the game reaches this date, however many turns that
    #: takes. Bounding by turns instead compared models at different dates: a
    #: model advancing 90 days a turn was scored six years past one advancing
    #: seven, against objectives that only grow with time (#50).
    until: str
    #: A backstop, not the bound. A model that never advances the clock has to
    #: stop somewhere, and a run that hits this never reached the finish line -
    #: the scorecard says so rather than scoring it as if it had.
    max_turns: int
    briefing: str
    objectives: list[Objective] = field(default_factory=list)
    seed: int = 1936
    #: The described opening position, for starts the mock cannot guess. None:
    #: the mock's own default, neutral 1936 Sweden.
    start_state: ScenarioStart | None = None
    #: How the scenario is meant to be fought: "llm" commands units directly,
    #: "ai" delegates operations to the game and commands intent. A scenario
    #: whose answer lives in the hybrid vocabulary has to say so, or the mode
    #: switch quietly removes the tools the answer needs.
    operational_control: str = "llm"


ECONOMY_RAMP = Scenario(
    key="economy_ramp",
    title="Peacetime economy ramp",
    country="SWE",
    start="1936-01-01",
    until="1936-10-01",
    max_turns=200,
    briefing=(
        "Neutral start, no war expected for years. Grow industry without wrecking "
        "stability, and keep a focus and all research slots busy."
    ),
    objectives=[
        Objective(
            "civ_factories_20",
            lambda s: s.civilian_factories >= 20,
            description="20+ civilian factories",
        ),
        Objective(
            "stability_held",
            lambda s: s.stability >= 0.50,
            description="stability at or above 50%",
        ),
        # Both of these used to read the final state, which measured when the
        # run happened to stop rather than how it was played: a focus is either
        # running or between focuses at any given instant, so the answer was
        # largely luck. Keeping them busy is a habit, so score the habit.
        Objective(
            "focus_kept_running",
            over=most_turns(lambda s: s.national_focus is not None),
            description="a focus was running on 80%+ of turns",
        ),
        Objective(
            "research_kept_busy",
            over=most_turns(lambda s: bool(s.research) and all(x.technology for x in s.research)),
            description="every research slot was busy on 80%+ of turns",
        ),
    ],
)

WAR_READINESS = Scenario(
    key="war_readiness",
    title="Ready by September 1939",
    country="SWE",
    start="1936-01-01",
    until="1939-09-01",
    max_turns=600,
    briefing=(
        "War arrives on 1939-09-01. Be able to equip and field a real army by then "
        "without having bankrupted the economy to do it."
    ),
    objectives=[
        Objective("mil_factories_15", lambda s: s.military_factories >= 15,
                  description="15+ military factories"),
        Objective("divisions_36", lambda s: sum(g.count for g in s.divisions) >= 36,
                  description="36+ divisions fielded"),
        Objective("equipment_buffer",
                  lambda s: s.stockpiles.get("infantry_equipment_1", 0) >= 10_000,
                  description="10k+ infantry equipment in reserve"),
    ],
)

DEFENSIVE_WAR = Scenario(
    key="defensive_war",
    title="Hold the Karelian front",
    country="FIN",
    start="1939-11-30",
    until="1940-03-13",
    max_turns=200,
    briefing=(
        "You are already at war with the Soviet Union, outnumbered worse than two "
        "to one on your only front, and it is losing ground. No alert will fire "
        "and no one is coming. The army you end this winter with is the army that "
        "can still defend Finland. Hold."
    ),
    start_state=ScenarioStart(
        stability=0.55,
        war_support=0.70,
        wars=[War(against="SOV", since="1939-11-30")],
        fronts=[
            Front(name="karelia", enemy="SOV", divisions_friendly=12, divisions_enemy=50,
                  stance="hold", pressure="losing_ground"),
        ],
        divisions=[
            DivisionGroup(template="Infantry", count=12, location="karelia"),
            DivisionGroup(template="Infantry", count=12, location="home"),
        ],
    ),
    objectives=[
        Objective(
            "front_held",
            lambda s: bool(s.fronts) and all(f.pressure != "losing_ground" for f in s.fronts),
            description="no front is losing ground at the end",
        ),
        Objective(
            "army_intact",
            lambda s: sum(g.count for g in s.divisions) >= 24,
            description="24+ divisions still fielded (the start had 24)",
        ),
    ],
    # Reinforcing and holding a collapsing front is the game AI's job; the
    # agent's job is to notice, reinforce and delegate. Direct orders alone
    # cannot solve this: there is not enough equipment to deploy parity.
    operational_control="ai",
)

RESOURCE_STARVED = Scenario(
    key="resource_starved",
    title="No oil, no rubber",
    country="SWE",
    start="1936-01-01",
    until="1936-10-01",
    max_turns=200,
    briefing=(
        "This country has no oil and no rubber, and nothing will ever alert you "
        "to it: the shortage sits in the resource line, quietly halving military "
        "output while everything else looks fine. Trade for what the country "
        "cannot mine, or synthesise it -- and keep production up anyway."
    ),
    start_state=ScenarioStart(resources={"oil": 0, "rubber": 0}),
    objectives=[
        Objective(
            "rubber_income",
            lambda s: s.resources.get("rubber", 0) > 0,
            description="rubber coming in again (trade or synthetics)",
        ),
        Objective(
            "production_kept_up",
            lambda s: s.stockpiles.get("infantry_equipment_1", 0) >= 8_000,
            description="8k+ infantry equipment despite the shortage",
        ),
    ],
)

REARMAMENT_RACE = Scenario(
    key="rearmament_race",
    title="Rearm before the war you know about",
    country="GER",
    start="1937-01-01",
    until="1939-09-01",
    max_turns=600,
    briefing=(
        "The war begins on 1938-06-01: about eighty weeks away. You start with six "
        "civilian factories, two military ones and a fraction of the army you will "
        "need. Construction runs one project at a time, so what you build first is "
        "what exists when the war arrives. Sequence accordingly."
    ),
    start_state=ScenarioStart(
        civilian_factories=6,
        military_factories=2,
        dockyards=0,
        production=[ProductionLine(equipment="infantry_equipment_1", factories=2)],
        stockpiles={"infantry_equipment_1": 1_500},
        divisions=[DivisionGroup(template="Infantry", count=4, location="home")],
        events={
            "1938-06-01": ("war", "The deadline arrives. Germany is at war.", "critical"),
        },
    ),
    objectives=[
        Objective("mil_factories_6", lambda s: s.military_factories >= 6,
                  description="6+ military factories by the war"),
        Objective("divisions_10", lambda s: sum(g.count for g in s.divisions) >= 10,
                  description="10+ divisions fielded"),
        Objective("equipment_buffer",
                  lambda s: s.stockpiles.get("infantry_equipment_1", 0) >= 2_000,
                  description="2k+ infantry equipment in reserve"),
    ],
)

RECOVERY = Scenario(
    key="recovery",
    title="A country run into the ground",
    country="SWE",
    start="1936-01-01",
    until="1936-10-01",
    max_turns=200,
    briefing=(
        "You inherit this country mid-rot: nothing is being built, no research is "
        "running, and the military factories are pointed at lines the army does "
        "not need while the infantry waits for equipment it does. Diagnose before "
        "you continue; the obvious defaults are the trap here."
    ),
    start_state=ScenarioStart(
        civilian_factories=10,
        military_factories=8,
        dockyards=2,
        production=[
            ProductionLine(equipment="support_equipment", factories=4, efficiency=0.6),
            ProductionLine(equipment="artillery", factories=2, efficiency=0.6),
        ],
        stockpiles={"infantry_equipment_1": 800},
    ),
    objectives=[
        Objective(
            "infantry_line_rebuilt",
            lambda s: any(line.equipment == "infantry_equipment_1" and line.factories >= 4
                          for line in s.production),
            description="the infantry line is running again (4+ factories)",
        ),
        Objective(
            "research_busy",
            lambda s: all(slot.technology for slot in s.research),
            description="no idle research slot at the end",
        ),
        Objective(
            "stockpile_rebuilt",
            lambda s: s.stockpiles.get("infantry_equipment_1", 0) >= 3_000,
            description="3k+ infantry equipment in reserve",
        ),
    ],
)

SCENARIOS = {s.key: s for s in (ECONOMY_RAMP, WAR_READINESS, DEFENSIVE_WAR,
                                RESOURCE_STARVED, REARMAMENT_RACE, RECOVERY)}
