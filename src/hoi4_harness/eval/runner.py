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


class PlaysetMismatch(ValueError):
    """A scenario was asked to run on a playset it was not written for."""


def run_scenario(
    scenario: Scenario | str,
    config: HarnessConfig | None = None,
    transcript_dir: Path | None = None,
    seed: int | None = None,
    operational_control: str | None = None,
) -> ScoreCard:
    """One run of one scenario. ``seed`` overrides the scenario's own mock seed,
    which is how a multi-seed run varies what actually varies."""
    if isinstance(scenario, str):
        if scenario not in SCENARIOS:
            raise KeyError(f"Unknown scenario {scenario!r}. Known: {', '.join(SCENARIOS)}")
        scenario = SCENARIOS[scenario]

    config = config or HarnessConfig()
    if config.handover or config.advisor:
        # Once a person acts too, a score says nothing about the model: outcome
        # attribution is gone. Refuse rather than produce a number that means
        # nothing.
        raise ValueError(
            "co-op runs (handover or advisor) are for playing, not scoring: "
            "a person's actions are in the outcome"
        )
    index = config.build_index()
    playset = index.playset.name if index is not None else "vanilla"
    if playset != scenario.playset:
        raise PlaysetMismatch(
            f"{scenario.key} is written for the playset '{scenario.playset}', and this run is "
            f"configured for '{playset}'. Its dates and objectives would score nonsense there."
        )
    # The scenario says how it is meant to be fought; a scenario whose answer
    # lives in the hybrid vocabulary cannot run with those tools switched off.
    # The experiment overrides this on purpose, to run one scenario all three
    # ways; nothing else should.
    config.operational_control = operational_control or scenario.operational_control
    adapter = MockAdapter(
        seed=scenario.seed if seed is None else seed,
        country=scenario.country,
        start=scenario.start,
        start_state=scenario.start_state,
    )
    env = HOI4Env(adapter, config, index=index)
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
    report = loop.run(scenario.max_turns, until=scenario.until)
    card = score(scenario, env.read_state(), report, loop.history)
    return attach_baseline(card, scenario)
