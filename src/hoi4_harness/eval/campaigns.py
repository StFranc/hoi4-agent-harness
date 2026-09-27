"""Long campaigns for measurement, not for scoring.

A scenario asks "did the agent do the thing"; a campaign asks "what did it cost
to play this stretch of history". Campaigns carry no objectives and no
baseline. They exist so ``hoi4-harness measure`` has something long and
representative to measure brief size and wake rate over (#14), peacetime and
wartime, without a game.

The wartime campaign scripts 1939-1941 from Germany's side at the mock's usual
level of crudeness: fronts open and close on their historical dates, the enemy
reinforces where it historically did, the autumn mud cuts supply, and the
winter counteroffensive arrives in December. Division counts are round numbers
chosen to exercise the front rules, not an order of battle. What it measures is
the harness -- how big a brief gets and how often the wake rule fires when a
war throws off wartime signals -- not how well anything plays.
"""

from __future__ import annotations

from ..types import DivisionGroup, Front, ProductionLine, ScenarioStart, War, WarChange
from .scenarios import Scenario


def _front(name: str, enemy: str, friendly: int, hostile: int) -> Front:
    return Front(name=name, enemy=enemy, divisions_friendly=friendly,
                 divisions_enemy=hostile, supply=1.0)


PEACETIME_1936_1939 = Scenario(
    key="peacetime_1936_1939",
    title="Neutral Sweden to the eve of war",
    country="SWE",
    start="1936-01-01",
    until="1939-08-31",
    max_turns=400,
    briefing="Grow the economy; no war is expected before 1939.",
)

WARTIME_1939_1941 = Scenario(
    key="wartime_1939_1941",
    title="Germany, September 1939 to December 1941",
    country="GER",
    start="1939-08-01",
    until="1941-12-31",
    max_turns=400,
    briefing=(
        "The war is coming and you know its shape. Keep the economy and the army "
        "in step with it, and react to what the fronts tell you."
    ),
    start_state=ScenarioStart(
        stability=0.7,
        war_support=0.6,
        political_power=150.0,
        manpower=1_500_000,
        civilian_factories=30,
        military_factories=28,
        dockyards=6,
        production=[ProductionLine(equipment="infantry_equipment_1", factories=20)],
        stockpiles={"infantry_equipment_1": 60_000},
        divisions=[DivisionGroup(template="Infantry", count=100, location="home")],
        timeline=[
            WarChange("1939-09-01", open_wars=[War(against="POL", since="1939-09-01")],
                      open_fronts=[_front("poland", "POL", 50, 40)]),
            WarChange("1939-09-03",
                      open_wars=[War(against="FRA", since="1939-09-03"),
                                 War(against="ENG", since="1939-09-03")],
                      open_fronts=[_front("westwall", "FRA", 30, 60)],
                      event=("war_declared", "Britain and France declare war.", "notable")),
            WarChange("1939-09-17", enemy_divisions={"poland": 15},
                      event=("invasion", "The Soviet Union invades eastern Poland.", "notable")),
            WarChange("1939-10-06", end_wars=["POL"], close_fronts=["poland"],
                      event=("capitulation", "Poland has capitulated.", "notable")),
            WarChange("1940-04-09", open_wars=[War(against="NOR", since="1940-04-09")],
                      open_fronts=[_front("norway", "NOR", 6, 6)]),
            WarChange("1940-05-10", open_fronts=[_front("ardennes", "FRA", 45, 30)],
                      enemy_divisions={"westwall": 55},
                      event=("offensive", "The western offensive begins.", "notable")),
            WarChange("1940-06-10", end_wars=["NOR"], close_fronts=["norway"]),
            WarChange("1940-06-22", end_wars=["FRA"], close_fronts=["westwall", "ardennes"],
                      event=("capitulation", "France has capitulated.", "notable")),
            WarChange("1941-04-06", open_wars=[War(against="YUG", since="1941-04-06")],
                      open_fronts=[_front("balkans", "YUG", 20, 30)]),
            WarChange("1941-04-17", end_wars=["YUG"], close_fronts=["balkans"]),
            WarChange("1941-05-01", reinforcements=60),
            WarChange("1941-06-22",
                      open_wars=[War(against="SOV", since="1941-06-22",
                                     allies=["FIN", "ROM", "HUN"])],
                      open_fronts=[_front("baltic", "SOV", 30, 40),
                                   _front("center", "SOV", 50, 60),
                                   _front("ukraine", "SOV", 40, 55)],
                      event=("war_declared", "War with the Soviet Union.", "critical")),
            WarChange("1941-09-01", enemy_divisions={"center": 90, "ukraine": 70}),
            WarChange("1941-10-15", supply={"center": 0.35, "ukraine": 0.45},
                      event=("weather", "The autumn mud swallows the roads.", "notable")),
            WarChange("1941-12-05", enemy_divisions={"center": 160},
                      event=("counteroffensive",
                             "The Soviet winter counteroffensive begins before Moscow.",
                             "critical")),
        ],
    ),
)

CAMPAIGNS = {c.key: c for c in (PEACETIME_1936_1939, WARTIME_1939_1941)}
