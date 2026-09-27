"""The hybrid experiment (#10): one scenario, three ways of commanding it.

The hybrid layer rests on a claim nobody has checked: that a model setting
strategy while the game's AI runs operations plays better than either alone.
This runs the same scenario, at the same seeds, three ways:

1. **model-only** -- ``operational_control=llm``: the model commands directly,
   reflexes on.
2. **AI-only** -- no model at all. The reflex layer hands every army to the
   game's AI and holds a defensive posture while a front is losing ground
   (``Policy._delegate``); nothing else decides anything. It costs nothing.
3. **hybrid** -- ``operational_control=ai`` with the ``hybrid`` guidance pack:
   the model sets intent, the AI executes, and delegating is the model's call.

and prints one table: median score and range, and what each arm spent. The
AI-only arm is the one that matters. If it scores close to hybrid, the model is
decorative on this scenario and the verdict line says so -- the honest outcome
the issue asks for, including "no difference".

Against the mock, the three arms measure the mock's crude theory of fronts and
whichever planner is configured. The command is the plumbing; the result worth
publishing needs a real game (#7, #8) and a real model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from ..config import HarnessConfig
from .scenarios import SCENARIOS, Scenario
from .variance import QUOTABLE_SEEDS, AggregateCard, run_seeds

ARMS = ("model-only", "AI-only", "hybrid")

#: A median within this of another counts as no difference. Crude on purpose:
#: with a handful of seeds, anything finer is reading tea leaves.
TIE = 0.05


def arm_config(arm: str, base: HarnessConfig) -> tuple[HarnessConfig, str]:
    """The configuration and control mode one arm runs with."""
    if arm == "model-only":
        return replace(base, planner_enabled=True, reflex_delegate=False), "llm"
    if arm == "AI-only":
        return replace(base, planner_enabled=False, reflex_delegate=True), "ai"
    if arm == "hybrid":
        return replace(base, planner_enabled=True, reflex_delegate=False, guidance="hybrid"), "ai"
    raise KeyError(f"unknown arm {arm!r}; arms are {', '.join(ARMS)}")


@dataclass
class Experiment:
    scenario: Scenario
    planner: str
    seeds: int
    arms: dict[str, AggregateCard] = field(default_factory=dict)

    def verdict(self) -> str:
        ai = self.arms["AI-only"].median
        hybrid = self.arms["hybrid"].median
        model = self.arms["model-only"].median
        if hybrid - ai <= TIE:
            return (f"hybrid ({hybrid:.2f}) does not beat AI-only ({ai:.2f}): on this scenario "
                    "the model is decorative")
        if hybrid - model <= TIE:
            return (f"hybrid ({hybrid:.2f}) beats AI-only ({ai:.2f}) but not model-only "
                    f"({model:.2f}): the split adds nothing over the model alone")
        return (f"hybrid ({hybrid:.2f}) beats both AI-only ({ai:.2f}) and model-only "
                f"({model:.2f}) by more than {TIE:.2f}")

    def render(self) -> str:
        rows = [["arm", "median", "range", "calls", "tokens in", "tokens out", "usd"]]
        for arm in ARMS:
            agg = self.arms[arm]
            median = lambda attr, agg=agg: sorted(getattr(c, attr) for c in agg.cards)[  # noqa: E731
                (agg.n - 1) // 2]
            rows.append([
                arm, f"{agg.median:.2f}", f"{agg.low:.2f}-{agg.high:.2f}",
                str(median("planner_calls")), f"{median('tokens_in'):,}",
                f"{median('tokens_out'):,}", f"${median('usd'):.2f}",
            ])
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        last = self.scenario.seed + self.seeds - 1
        lines = [
            f"{self.scenario.key}: model-only vs AI-only vs hybrid",
            f"fixed: seeds={self.scenario.seed}..{last} (n={self.seeds}) planner={self.planner}",
            "",
        ]
        for index, row in enumerate(rows):
            lines.append("  ".join(
                cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                for i, cell in enumerate(row)
            ).rstrip())
            if index == 0:
                lines.append("  ".join("-" * w for w in widths))
        lines.append("")
        lines.append("verdict: " + self.verdict())
        if self.seeds < QUOTABLE_SEEDS:
            lines.append(f"(fewer than {QUOTABLE_SEEDS} seeds: an anecdote, not a result)")
        if self.planner.startswith("scripted"):
            lines.append("(scripted planner: this exercises the plumbing, it measures no model)")
        return "\n".join(lines)


def run_experiment(
    scenario: str | Scenario,
    config: HarnessConfig | None = None,
    seeds: int = 5,
    transcript_dir: Path | None = None,
) -> Experiment:
    if isinstance(scenario, str):
        if scenario not in SCENARIOS:
            raise KeyError(f"Unknown scenario {scenario!r}. Known: {', '.join(SCENARIOS)}")
        scenario = SCENARIOS[scenario]
    config = config or HarnessConfig()
    result = Experiment(
        scenario=scenario, seeds=max(1, seeds),
        planner=f"{config.planner.provider}:{config.planner.model}",
    )
    for arm in ARMS:
        arm_cfg, control = arm_config(arm, config)
        where = transcript_dir / arm if transcript_dir else None
        result.arms[arm] = run_seeds(scenario, arm_cfg, result.seeds, where,
                                     operational_control=control)
    return result
