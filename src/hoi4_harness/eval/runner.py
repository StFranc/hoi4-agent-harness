"""Run a scenario end to end and score it."""

from __future__ import annotations

from pathlib import Path

from ..adapters.mock import MockAdapter
from ..agent.llm import build_llm
from ..agent.loop import AgentLoop
from ..config import HarnessConfig
from ..env import HOI4Env
from .baselines import attach_baseline
from .metrics import ScoreCard, score
from .scenarios import SCENARIOS, Scenario


def run_scenario(
    scenario: Scenario | str,
    config: HarnessConfig | None = None,
    transcript_dir: Path | None = None,
) -> ScoreCard:
    if isinstance(scenario, str):
        if scenario not in SCENARIOS:
            raise KeyError(f"Unknown scenario {scenario!r}. Known: {', '.join(SCENARIOS)}")
        scenario = SCENARIOS[scenario]

    config = config or HarnessConfig()
    # The scenario says how it is meant to be fought; a scenario whose answer
    # lives in the hybrid vocabulary cannot run with those tools switched off.
    config.operational_control = scenario.operational_control
    adapter = MockAdapter(
        seed=scenario.seed,
        country=scenario.country,
        start=scenario.start,
        start_state=scenario.start_state,
    )
    env = HOI4Env(adapter, config)
    env.reset()

    transcript = (
        (transcript_dir / f"{scenario.key}.jsonl") if transcript_dir else None
    )
    loop = AgentLoop(
        env=env,
        # planner_enabled=False runs the scenario on reflexes alone: no client is
        # built, so a baseline run costs nothing and needs no API key.
        planner=build_llm(config.planner) if config.planner_enabled else None,
        config=config,
        transcript_path=transcript,
    )
    loop.config.objective = scenario.briefing
    report = loop.run(scenario.turns)
    card = score(scenario, env.read_state(), report)
    return attach_baseline(card, scenario)
