"""The recorded reflex-only floor every score is reported against.

A score of 0.75 means nothing on its own: the reflex layer in
``agent/policy.py`` plays for free, so a model is only impressive if it beats
what deterministic housekeeping achieves alone on the same scenario.
``eval --no-llm`` measures that, ``--write-baseline`` records it into
``baselines.json`` (committed, and shipped inside the package), and every
ScoreCard is then rendered as a delta over it.

A baseline is only meaningful for the scenario definition it was measured
against, so each record carries that definition's objective names. When the
scenario's objectives change, the record is ignored rather than compared
against -- a stale number is worse than no number.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from .metrics import ScoreCard
from .scenarios import Scenario

# Lives next to this module so it ships with the wheel.
BASELINE_PATH = Path(__file__).with_name("baselines.json")


def load(path: Path | None = None) -> dict:
    """The recorded baselines, keyed by scenario. No file yet: nothing recorded."""
    path = path or BASELINE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def for_scenario(scenario: Scenario, data: dict | None = None) -> tuple[float | None, str | None]:
    """The recorded score for one scenario, plus why there may be none.

    Returns ``(score, note)``. The note is set only when a record exists but must
    be ignored: it was measured against objectives the scenario no longer has.
    """
    record = (data if data is not None else load()).get(scenario.key)
    if record is None:
        return None, None

    recorded = sorted(record.get("objectives", {}))
    current = sorted(objective.name for objective in scenario.objectives)
    if recorded != current:
        note = (
            "baseline ignored: recorded objectives no longer match the scenario "
            f"(recorded: {', '.join(recorded)}; now: {', '.join(current)}). "
            "Re-record with eval --no-llm --write-baseline."
        )
        return None, note
    return float(record["score"]), None


def attach_baseline(card: ScoreCard, scenario: Scenario, data: dict | None = None) -> ScoreCard:
    """Fill in ``card.baseline`` / ``card.baseline_note`` from the recorded file."""
    card.baseline, card.baseline_note = for_scenario(scenario, data)
    return card


def write_baselines(cards: dict[str, ScoreCard], path: Path | None = None) -> None:
    """Record reflex-only scores, merged into what is already recorded.

    Re-recording a single scenario must not drop the rest, so entries are
    replaced per key and everything else is kept.
    """
    data = load(path)
    for key, card in cards.items():
        data[key] = {
            "score": card.score,
            "objectives": dict(card.objectives),
            "turns": card.turns,
            "harness_version": __version__,
        }
    target = path or BASELINE_PATH
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
