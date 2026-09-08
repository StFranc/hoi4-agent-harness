"""Configuration, resolved from CLI flags over environment over defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Per-provider defaults, used when no model is named. Nothing here is special-
# cased in the code: any model string the provider accepts will do.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-5",
    "google": "gemini-2.5-pro",
    "scripted": "scripted",
}

DEFAULT_FAST_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
    "google": "gemini-2.5-flash",
    "scripted": "scripted",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class LLMConfig:
    """One model role. The harness uses two: a planner and a cheap triage model."""

    provider: str = "scripted"
    model: str = ""
    base_url: str | None = None          # OpenAI-compatible endpoints: Ollama, vLLM, OpenRouter
    api_key_env: str | None = None
    max_tokens: int = 16000
    effort: str = "high"                 # reasoning effort, where the provider supports it
    temperature: float | None = None
    cache_prefix: bool = True            # cache the stable system+tools prefix, where supported
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model:
            self.model = DEFAULT_MODELS.get(self.provider, "")

    @classmethod
    def from_env(cls, prefix: str = "HOI4_LLM", fast: bool = False) -> LLMConfig:
        provider = os.environ.get(f"{prefix}_PROVIDER", "scripted").strip().lower()
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        if not model:
            table = DEFAULT_FAST_MODELS if fast else DEFAULT_MODELS
            model = table.get(provider, "")
        return cls(
            provider=provider,
            model=model,
            base_url=os.environ.get(f"{prefix}_BASE_URL") or None,
            api_key_env=os.environ.get(f"{prefix}_API_KEY_ENV") or None,
            max_tokens=_env_int(f"{prefix}_MAX_TOKENS", 4000 if fast else 16000),
            effort=os.environ.get(f"{prefix}_EFFORT", "low" if fast else "high").strip(),
            temperature=_env_float(f"{prefix}_TEMPERATURE"),
        )


@dataclass
class BudgetConfig:
    """Hard ceilings for one run. The loop degrades to the reflex policy when a
    ceiling is hit rather than stopping the game."""

    max_llm_calls: int = 250
    max_input_tokens: int = 4_000_000
    max_output_tokens: int = 400_000
    max_usd: float | None = None
    usd_per_m_input: float = 0.0         # set from your provider's price sheet
    usd_per_m_output: float = 0.0

    @classmethod
    def from_env(cls) -> BudgetConfig:
        return cls(
            max_llm_calls=_env_int("HOI4_MAX_LLM_CALLS", 250),
            max_input_tokens=_env_int("HOI4_MAX_INPUT_TOKENS", 4_000_000),
            max_output_tokens=_env_int("HOI4_MAX_OUTPUT_TOKENS", 400_000),
            max_usd=_env_float("HOI4_MAX_USD"),
            usd_per_m_input=_env_float("HOI4_USD_PER_M_INPUT", 0.0) or 0.0,
            usd_per_m_output=_env_float("HOI4_USD_PER_M_OUTPUT", 0.0) or 0.0,
        )


@dataclass
class HarnessConfig:
    adapter: str = "mock"
    planner: LLMConfig = field(default_factory=LLMConfig)
    triage: LLMConfig = field(default_factory=lambda: LLMConfig(max_tokens=1000, effort="low"))
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    # --- what the model is told ---------------------------------------------
    # A built-in pack name ("none", "unconstrained", "minimal", "doctrine",
    # "historical", "coach") or a path to your own file. `system_prompt_path`
    # replaces the whole prompt, mechanics included.
    guidance: str = "doctrine"
    system_prompt_extra: str = ""
    system_prompt_path: Path | None = None
    objective: str = (
        "Build a defensible economy, keep stability and war support healthy, and do not "
        "enter a war you cannot supply."
    )

    # --- what the model may do ----------------------------------------------
    # `enabled_actions` is a whitelist (None = everything the adapter supports);
    # `disabled_actions` subtracts from it. `require_confirmation` False lets
    # irreversible actions through without a gate -- only meaningful with --live.
    enabled_actions: list[str] | None = None
    disabled_actions: list[str] = field(default_factory=list)
    require_confirmation: bool = True

    # --- pacing -------------------------------------------------------------
    # The harness owns the clock. It pauses the game, decides, then runs forward
    # `days_per_turn` in-game days before looking again. A critical event cuts
    # the wait short.
    days_per_turn: int = 7
    max_actions_per_turn: int = 8
    max_tool_rounds_per_turn: int = 3
    #: Consecutive provider failures tolerated before the run stops. A fatal
    #: error (bad key, rejected schema) stops immediately regardless.
    max_consecutive_llm_errors: int = 5
    poll_seconds: float = 2.0
    full_brief_every: int = 8
    wake_on_free_research_slot: bool = True
    wake_on_no_focus: bool = True
    reflex_enabled: bool = True

    # --- run ----------------------------------------------------------------
    turns: int = 10
    dry_run: bool = True
    run_dir: Path = Path("runs")
    save_dir: Path | None = None
    log_path: Path | None = None          # game.log, for the logtail adapter
    window_title: str = "Hearts of Iron IV"
    # Which layer owns operations. "llm" = the model moves every army;
    # "ai" = the native AI runs fronts and the model sets intent (hybrid).
    operational_control: str = "llm"
    # Who owns the game clock. "harness" pauses, decides, and runs the game
    # forward itself. "player" never touches it -- required when a person is
    # playing the same campaign, since being paused mid-battle by your own
    # tooling is worse than having no tooling.
    clock_owner: str = "harness"
    country: str = "SWE"
    start_date: str = "1936-01-01"
    seed: int = 1936

    @classmethod
    def from_env(cls) -> HarnessConfig:
        save_dir = os.environ.get("HOI4_SAVE_DIR")
        log_path = os.environ.get("HOI4_LOG_PATH")
        system_prompt_path = os.environ.get("HOI4_SYSTEM_PROMPT")
        return cls(
            adapter=os.environ.get("HOI4_ADAPTER", "mock").strip().lower(),
            planner=LLMConfig.from_env("HOI4_LLM"),
            triage=LLMConfig.from_env("HOI4_TRIAGE", fast=True),
            budget=BudgetConfig.from_env(),
            guidance=os.environ.get("HOI4_GUIDANCE", "doctrine").strip(),
            system_prompt_extra=os.environ.get("HOI4_SYSTEM_PROMPT_EXTRA", ""),
            system_prompt_path=Path(system_prompt_path) if system_prompt_path else None,
            objective=os.environ.get("HOI4_OBJECTIVE") or cls.objective,
            require_confirmation=_env_bool("HOI4_REQUIRE_CONFIRMATION", True),
            days_per_turn=_env_int("HOI4_DAYS_PER_TURN", 7),
            max_actions_per_turn=_env_int("HOI4_MAX_ACTIONS_PER_TURN", 8),
            max_tool_rounds_per_turn=_env_int("HOI4_MAX_TOOL_ROUNDS", 3),
            max_consecutive_llm_errors=_env_int("HOI4_MAX_CONSECUTIVE_LLM_ERRORS", 5),
            full_brief_every=_env_int("HOI4_FULL_BRIEF_EVERY", 8),
            poll_seconds=_env_float("HOI4_POLL_SECONDS", 2.0) or 2.0,
            turns=_env_int("HOI4_TURNS", 10),
            dry_run=_env_bool("HOI4_DRY_RUN", True),
            run_dir=Path(os.environ.get("HOI4_RUN_DIR", "runs")),
            save_dir=Path(save_dir) if save_dir else None,
            log_path=Path(log_path) if log_path else None,
            window_title=os.environ.get("HOI4_WINDOW_TITLE", "Hearts of Iron IV"),
            operational_control=os.environ.get("HOI4_OPERATIONAL_CONTROL", "llm").strip(),
            clock_owner=os.environ.get("HOI4_CLOCK_OWNER", "harness").strip(),
            country=os.environ.get("HOI4_COUNTRY", "SWE"),
            start_date=os.environ.get("HOI4_START_DATE", "1936-01-01"),
            seed=_env_int("HOI4_SEED", 1936),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> HarnessConfig:
        """Load a profile: one JSON or TOML file holding the whole configuration.

        Environment defaults are applied first, so a profile only has to state
        what it changes. Nested ``planner``/``triage``/``budget`` tables are
        merged rather than replaced.
        """
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".toml":
            import tomllib

            data = tomllib.loads(raw)
        else:
            import json

            data = json.loads(raw)
        return cls.from_env().merge(data)

    def merge(self, data: dict) -> HarnessConfig:
        """Apply a nested dict of overrides, returning self."""
        for key, value in data.items():
            if key in {"planner", "triage"} and isinstance(value, dict):
                role = getattr(self, key)
                for sub_key, sub_value in value.items():
                    setattr(role, sub_key, sub_value)
                role.__post_init__()
            elif key == "budget" and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    setattr(self.budget, sub_key, sub_value)
            elif key in {"run_dir", "save_dir", "system_prompt_path"} and value is not None:
                setattr(self, key, Path(value))
            elif hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(f"Unknown configuration key {key!r}")
        return self

    def allowed_actions(self, adapter_supports: set[str]) -> set[str]:
        """Adapter capability, narrowed by control mode then by the operator.

        The control mode goes first so that an explicit whitelist can never
        smuggle a direct army order into a run where the native AI is executing.
        """
        from .agent.hybrid import actions_for_mode

        allowed = actions_for_mode(set(adapter_supports), self.operational_control)
        if self.clock_owner != "harness":
            # The player owns the clock, so the model may not take it either --
            # structurally, not by asking it nicely in the prompt.
            allowed -= {"set_game_speed"}
        if self.enabled_actions is not None:
            allowed &= set(self.enabled_actions)
        return allowed - set(self.disabled_actions)
