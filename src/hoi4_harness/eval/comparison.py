"""Cross-model comparison: one scenario, several models, one table.

``eval`` scores one configuration at a time. Choosing a model then means running
the command once per candidate and holding the ScoreCards in your head, which is
exactly how the spend column gets lost. ``compare`` runs the same scenario once
per model and prints the rows together.

The table refuses to hide the two things cross-model comparisons usually lie
with. Spend is printed beside score, because a model that scores 0.9 for four
hundred calls has not beaten one that scores 0.8 for forty. And the fixed
configuration is printed above the table, because guidance moves scores more
than model choice does at the small end -- an unstated pack makes every row
uninterpretable.

One model failing must not cost the others their run. A provider that cannot
even start (no key, no SDK, a typo'd provider) becomes a failed row and the
comparison carries on; a run the provider killed partway keeps its partial score
but says so under the row.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from ..agent.errors import describe
from ..config import HarnessConfig, LLMConfig
from .baselines import for_scenario
from .metrics import ScoreCard
from .runner import run_scenario
from .scenarios import SCENARIOS, Scenario


@dataclass
class ModelResult:
    """One model's outcome, or why there is none."""

    model: str
    card: ScoreCard | None = None
    #: Set when the model could not run at all; the row renders as dashes.
    error: str | None = None


@dataclass
class Comparison:
    """Every model's result, plus the fixed configuration and baseline they answer to."""

    scenario: Scenario
    config: HarnessConfig
    results: list[ModelResult] = field(default_factory=list)
    baseline: float | None = None
    baseline_note: str | None = None

    def render(self) -> str:
        header = ["model", "score", "delta", "calls", "tokens in", "tokens out", "usd"]
        keyed = [(result, self._row(result)) for result in self.results]
        baseline_cells = self._baseline_row() if self.baseline is not None else None
        body = ([baseline_cells] if baseline_cells is not None else []) + [
            cells for _, cells in keyed
        ]
        widths = [max(len(header[i]), *(len(row[i]) for row in body)) for i in range(len(header))]

        count = len(self.results)
        lines = [
            f"{self.scenario.key} across {count} model{'s' if count != 1 else ''}",
            self._fixed_line(),
            "",
            _row(header, widths),
            "  ".join("-" * width for width in widths),
        ]
        if baseline_cells is not None:
            lines.append(_row(baseline_cells, widths))
        for result, cells in keyed:
            lines.append(_row(cells, widths))
            if result.error:
                lines.append(f"  failed: {result.error}")
            elif result.card and result.card.stopped_reason:
                lines.append(f"  stopped early: {result.card.stopped_reason}")
        if self.baseline_note:
            lines.append(f"  {self.baseline_note}")
        return "\n".join(lines)

    def _row(self, result: ModelResult) -> list[str]:
        card = result.card
        if card is None:
            return [result.model, "--", "--", "--", "--", "--", "--"]
        delta = "--" if card.baseline is None else f"{card.score - card.baseline:+.2f}"
        return [
            result.model,
            f"{card.score:.2f}",
            delta,
            str(card.planner_calls),
            f"{card.tokens_in:,}",
            f"{card.tokens_out:,}",
            f"${card.usd:.2f}",
        ]

    def _baseline_row(self) -> list[str]:
        # The reflex layer plays without a model, so zero spend is true by
        # construction, not an estimate.
        return ["baseline (reflex)", f"{self.baseline:.2f}", "--", "0", "0", "0", "$0.00"]

    def _fixed_line(self) -> str:
        # The levers that move a score besides model choice. Any of them differing
        # between rows would make the comparison meaningless, so they are stated
        # rather than assumed.
        config = self.config
        return (
            f"fixed for every run: scenario={self.scenario.key} seed={self.scenario.seed} "
            f"guidance={config.system_prompt_path or config.guidance} "
            f"control={config.operational_control} days_per_turn={config.days_per_turn}"
        )


def _row(cells: list[str], widths: list[int]) -> str:
    aligned = [cells[0].ljust(widths[0])] + [
        cell.rjust(width) for cell, width in zip(cells[1:], widths[1:], strict=True)
    ]
    return "  ".join(aligned).rstrip()


def compare(
    scenario: str | Scenario,
    models: list[str] | None = None,
    config: HarnessConfig | None = None,
) -> Comparison:
    """Run one scenario once per model spec and return the comparison to render.

    Each spec is ``provider:model``; the model half may itself contain colons
    (``openai:qwen2.5:14b``). With no specs the configured planner is run, so
    ``compare economy_ramp`` still answers the first question worth asking: does
    the model beat the reflexes at all?
    """
    if isinstance(scenario, str):
        if scenario not in SCENARIOS:
            raise KeyError(f"Unknown scenario {scenario!r}. Known: {', '.join(SCENARIOS)}")
        scenario = SCENARIOS[scenario]

    config = config or HarnessConfig()
    if not models:
        models = [f"{config.planner.provider}:{config.planner.model}"]
    baseline, baseline_note = for_scenario(scenario)
    return Comparison(
        scenario=scenario,
        config=config,
        results=[_run_one(scenario, spec, config) for spec in models],
        baseline=baseline,
        baseline_note=baseline_note,
    )


def planner_for(spec: str, base: LLMConfig) -> LLMConfig:
    """One model entry as a planner config, sharing the base run's every other setting.

    A bare provider keeps the configured model when it names the configured
    provider, and takes the provider's own default otherwise -- so the entry
    never silently sends one provider another provider's model id.
    """
    provider, sep, model = spec.strip().partition(":")
    if not sep:
        model = base.model if provider == base.provider else ""
    return replace(base, provider=provider, model=model)


def _run_one(scenario: Scenario, spec: str, config: HarnessConfig) -> ModelResult:
    planner = planner_for(spec, config.planner)
    try:
        card = run_scenario(
            scenario, replace(config, planner=planner), transcript_dir=_transcripts(config, planner)
        )
    except Exception as exc:  # noqa: BLE001 - the provider is not ours; a failure is a row
        return ModelResult(model=_label(planner), error=describe(exc))
    return ModelResult(model=_label(planner), card=card)


def _transcripts(config: HarnessConfig, planner: LLMConfig) -> Path:
    # One directory per model: run_scenario names the file after the scenario, so
    # a shared directory would have every model's transcript overwrite the last.
    return config.run_dir / "".join(
        c if c.isalnum() or c in "-._" else "_" for c in _label(planner)
    )


def _label(planner: LLMConfig) -> str:
    return f"{planner.provider}:{planner.model}"
