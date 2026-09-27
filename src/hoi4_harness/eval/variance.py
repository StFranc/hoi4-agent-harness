"""Many seeds, one claim: report the spread, not a single run.

One run of one scenario against one model is an anecdote. The mock is
deterministic for a fixed seed, but the model is not, and neither is a real
game. So a result meant to be quoted is N runs of the same (model, scenario,
control mode) at different seeds, reported as a median with its range.

What varies between seeds is what the harness can vary: the mock's RNG (and,
once the harness drives a real game, the game's own). A model sampling at
temperature varies on its own.

Rule of thumb, repeated in docs/customization.md: a claim that goes in a README
needs at least five seeds, and "A beats B" needs A's worst run to beat B's
median at the very least -- ranges that overlap heavily are a tie, whatever the
medians say.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ..config import HarnessConfig
from .metrics import ScoreCard
from .runner import run_scenario
from .scenarios import Scenario

#: Fewer seeds than this and a result is labelled as not yet quotable.
QUOTABLE_SEEDS = 5


@dataclass
class AggregateCard:
    """N score cards for one configuration, and the spread across them."""

    scenario: str
    seeds: list[int] = field(default_factory=list)
    cards: list[ScoreCard] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.cards)

    @property
    def scores(self) -> list[float]:
        return [card.score for card in self.cards]

    @property
    def median(self) -> float:
        return statistics.median(self.scores)

    @property
    def low(self) -> float:
        return min(self.scores)

    @property
    def high(self) -> float:
        return max(self.scores)

    @property
    def baseline(self) -> float | None:
        return self.cards[0].baseline if self.cards else None

    def _median_of(self, attribute: str) -> float:
        return statistics.median(getattr(card, attribute) for card in self.cards)

    def render(self) -> str:
        head = (
            f"{self.scenario}: median {self.median:.2f} "
            f"[{self.low:.2f}-{self.high:.2f}] over {self.n} seed{'s' if self.n != 1 else ''}"
        )
        if self.baseline is not None:
            head += f" (baseline {self.baseline:.2f}, {self.median - self.baseline:+.2f})"
        lines = [head]
        if self.n < QUOTABLE_SEEDS:
            lines.append(f"  (fewer than {QUOTABLE_SEEDS} seeds: an anecdote, not a result)")
        for name in self.cards[0].objectives if self.cards else []:
            passed = sum(1 for card in self.cards if card.objectives.get(name))
            lines.append(f"  [{passed}/{self.n}] {name}")
        lines.append(
            f"  median cost: {self._median_of('planner_calls'):.0f} model calls, "
            f"{self._median_of('tokens_in'):,.0f} in / {self._median_of('tokens_out'):,.0f} out"
            + (f", ${self._median_of('usd'):.2f}" if any(c.usd for c in self.cards) else "")
        )
        short = sum(1 for card in self.cards if card.reached_end_date is False)
        stopped = sum(1 for card in self.cards if card.stopped_reason)
        if short:
            lines.append(f"  {short}/{self.n} runs STOPPED SHORT of the end date")
        if stopped:
            lines.append(f"  {stopped}/{self.n} runs ended early on provider errors")
        lines.append("  seeds: " + ", ".join(str(seed) for seed in self.seeds))
        return "\n".join(lines)


def seeds_for(scenario: Scenario, count: int) -> list[int]:
    """Consecutive seeds starting at the scenario's own, so seed 1 of N is the
    single run everyone already has."""
    return [scenario.seed + offset for offset in range(max(1, count))]


def run_seeds(
    scenario: Scenario,
    config: HarnessConfig,
    count: int,
    transcript_dir: Path | None = None,
    operational_control: str | None = None,
) -> AggregateCard:
    aggregate = AggregateCard(scenario=scenario.key)
    for seed in seeds_for(scenario, count):
        # One directory per seed: run_scenario names the file after the
        # scenario, so a shared directory would keep only the last seed's run.
        where = transcript_dir / f"seed-{seed}" if transcript_dir else None
        aggregate.seeds.append(seed)
        aggregate.cards.append(run_scenario(
            scenario, config, transcript_dir=where, seed=seed,
            operational_control=operational_control,
        ))
    return aggregate
