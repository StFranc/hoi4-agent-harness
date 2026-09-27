"""Spend tracking and hard ceilings for a run.

A campaign is long and a loop is patient, which is exactly the combination that
produces a surprise bill. The budget is checked before every model call; when it
is exhausted the loop does not stop the game, it drops to the reflex policy and
keeps playing for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import BudgetConfig
from .llm.base import Usage


@dataclass
class Spend:
    calls: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def usd(self) -> float:
        return self._usd

    _usd: float = 0.0


class BudgetGuard:
    def __init__(self, config: BudgetConfig):
        self.config = config
        self.spend = Spend()
        self.stopped_reason: str | None = None

    def record(self, usage: Usage) -> None:
        self.spend.calls += 1
        self.spend.usage = self.spend.usage + usage
        self.spend._usd += self.price(usage)

    def price(self, usage: Usage) -> float:
        """Dollars for one call: uncached input, cache reads, cache writes and
        output each at their own rate. A rate left unset falls back to the input
        rate, so an unconfigured cache is never priced as free."""
        c = self.config
        cached = c.usd_per_m_input if c.usd_per_m_cached_input is None else c.usd_per_m_cached_input
        write = c.usd_per_m_input if c.usd_per_m_cache_write is None else c.usd_per_m_cache_write
        return (
            usage.uncached_input_tokens * c.usd_per_m_input
            + usage.cached_input_tokens * cached
            + usage.cache_write_input_tokens * write
            + usage.output_tokens * c.usd_per_m_output
        ) / 1_000_000

    @property
    def priced(self) -> bool:
        """False when no rate was ever set, so '$0.00' means 'unpriced', not 'free'."""
        return bool(self.config.usd_per_m_input or self.config.usd_per_m_output)

    def allows_call(self) -> bool:
        return self.why_blocked() is None

    def why_blocked(self) -> str | None:
        c, s = self.config, self.spend
        if s.calls >= c.max_llm_calls:
            return f"call ceiling reached ({c.max_llm_calls})"
        if s.usage.input_tokens >= c.max_input_tokens:
            return f"input-token ceiling reached ({c.max_input_tokens:,})"
        if s.usage.output_tokens >= c.max_output_tokens:
            return f"output-token ceiling reached ({c.max_output_tokens:,})"
        if c.max_usd is not None and s.usd >= c.max_usd:
            return f"spend ceiling reached (${c.max_usd:.2f})"
        return None

    def summary(self) -> dict:
        s = self.spend
        return {
            "llm_calls": s.calls,
            "input_tokens": s.usage.input_tokens,
            "cached_input_tokens": s.usage.cached_input_tokens,
            "cache_write_input_tokens": s.usage.cache_write_input_tokens,
            "output_tokens": s.usage.output_tokens,
            "usd": round(s.usd, 4),
            "priced": self.priced,
            "blocked": self.why_blocked(),
        }
