"""Measure what a run cost, split peacetime vs wartime, from its transcript.

``docs/cost-and-timing.md`` began as an estimate made in peacetime, where
nothing happens. War is where brief size and wake rate rise together, which is
the compounding case, so this reads a finished transcript and reports, per
phase:

* game days and turns spent in it, and how many turns woke the model;
* wakes per game-month -- the rate the budget actually has to fund;
* brief size in tokens (mean, median, max; full briefs and deltas apart), with
  the counting method, since chars/4 and a real tokenizer disagree;
* what the first request of each wake sends, split the way it bills: the
  cacheable prefix (system prompt and tools) and the per-turn message;
* provider-reported input tokens per wake and the share served from cache,
  when the transcript has real ``llm`` usage in it;
* the wake reasons, most frequent first, because a wake rate is only
  actionable once you know which rule is spending it.

Everything is read from records the loop already writes; nothing is inferred
from characters that the transcript counted directly.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

DAYS_PER_MONTH = 30.44


@dataclass
class Phase:
    name: str
    days: int = 0
    turns: int = 0
    wakes: int = 0
    brief_tokens: list[int] = field(default_factory=list)
    full_tokens: list[int] = field(default_factory=list)
    delta_tokens: list[int] = field(default_factory=list)
    prefix_tokens: list[int] = field(default_factory=list)
    turn_tokens: list[int] = field(default_factory=list)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    reasons: Counter = field(default_factory=Counter)

    @property
    def wakes_per_month(self) -> float:
        return self.wakes / (self.days / DAYS_PER_MONTH) if self.days else 0.0

    @property
    def cache_hit(self) -> float | None:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else None


@dataclass
class Measurement:
    source: str
    token_methods: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    phases: dict[str, Phase] = field(default_factory=dict)

    def phase(self, at_war: bool) -> Phase:
        name = "wartime" if at_war else "peacetime"
        return self.phases.setdefault(name, Phase(name))

    def render(self) -> str:
        methods = ", ".join(sorted(self.token_methods)) or "none recorded"
        lines = [f"measured from {self.source} (brief tokens counted by: {methods})", ""]
        header = ["phase", "days", "turns", "wakes", "wakes/mo", "brief mean", "median",
                  "max", "full mean", "delta mean", "prefix", "turn msg", "billed in/wake",
                  "cache hit"]
        rows = [header]
        # The scripted stand-in reports a made-up usage (brief length / 4) and
        # no caching; printing it as billed tokens would be a fabricated number.
        real = bool(self.models - {"scripted"})
        for name in ("peacetime", "wartime"):
            p = self.phases.get(name)
            if p is None:
                continue
            rows.append([
                name, str(p.days), str(p.turns), str(p.wakes), f"{p.wakes_per_month:.1f}",
                _mean(p.brief_tokens), _median(p.brief_tokens), _max(p.brief_tokens),
                _mean(p.full_tokens), _mean(p.delta_tokens),
                _mean(p.prefix_tokens), _mean(p.turn_tokens),
                f"{p.input_tokens / p.wakes:,.0f}" if p.wakes and p.input_tokens and real else "--",
                f"{p.cache_hit:.0%}" if p.cache_hit is not None and real else "--",
            ])
        widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
        for index, row in enumerate(rows):
            lines.append("  ".join(
                cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                for i, cell in enumerate(row)
            ).rstrip())
            if index == 0:
                lines.append("  ".join("-" * w for w in widths))
        if self.models and not real:
            lines.append("(billed tokens and cache hits need a real provider; this run was scripted)")
        for name in ("peacetime", "wartime"):
            p = self.phases.get(name)
            if p is None or not p.reasons:
                continue
            top = ", ".join(f"{reason} x{n}" for reason, n in p.reasons.most_common(5))
            lines.append("")
            lines.append(f"{name} wake reasons: {top}")
        return "\n".join(lines)


def measure(path: str | Path) -> Measurement:
    path = Path(path)
    result = Measurement(source=str(path))
    current: Phase | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a run that died mid-write leaves a torn last line
        kind = record.get("kind")
        if kind == "observe":
            current = result.phase(bool(record.get("at_war")))
            tokens = record.get("brief_tokens")
            if tokens is None:
                continue  # a transcript from before briefs were counted
            result.token_methods.add(record.get("token_method", "unknown"))
            current.brief_tokens.append(tokens)
            (current.delta_tokens if record.get("is_delta") else current.full_tokens).append(tokens)
            if "prompt_prefix_tokens" in record:
                current.prefix_tokens.append(record["prompt_prefix_tokens"])
                current.turn_tokens.append(record["prompt_turn_tokens"])
            reason = str(record.get("wake_reason", ""))
            current.reasons[_reason_key(reason)] += 1
        elif kind == "llm" and current is not None:
            usage = record.get("usage") or {}
            result.models.add(str(record.get("model", "")))
            current.calls += 1
            current.input_tokens += int(usage.get("input_tokens", 0))
            current.cached_input_tokens += int(usage.get("cached_input_tokens", 0))
            current.output_tokens += int(usage.get("output_tokens", 0))
        elif kind == "turn_end" and "at_war" in record:
            phase = result.phase(bool(record["at_war"]))
            phase.turns += 1
            phase.wakes += bool(record.get("woke"))
            phase.days += _days(record.get("from_date"), record.get("date"))
    return result


def _reason_key(reason: str) -> str:
    """Group wake reasons by rule, not by the specifics each one carries."""
    for prefix in ("critical event", "encirclement forming", "supply collapsed",
                   "the capital is threatened", "an ally", "front "):
        if reason.startswith(prefix):
            return prefix.strip()
    if "research slot" in reason:
        return "research slot idle"
    return reason


def _days(start: str | None, end: str | None) -> int:
    try:
        return max(0, (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days)
    except (TypeError, ValueError):
        return 0


def _mean(values: list[int]) -> str:
    return f"{statistics.fmean(values):.0f}" if values else "--"


def _median(values: list[int]) -> str:
    return f"{statistics.median(values):.0f}" if values else "--"


def _max(values: list[int]) -> str:
    return str(max(values)) if values else "--"
