"""hoi4-agent-harness: drive Hearts of Iron IV from an LLM agent loop.

Four seams, deliberately kept apart:

* an *adapter* that reads game state and applies actions (mock / savegame / screen),
* an *action catalog* that is the entire vocabulary the agent is allowed to use,
* a *policy* layer that decides when a decision is worth an LLM call at all, and
* an *LLM client* that is provider-neutral: Anthropic, any OpenAI-compatible
  endpoint (OpenAI, Ollama, vLLM, OpenRouter, ...), Gemini, or a scripted stub.

Nothing in the core imports a vendor SDK; providers are loaded lazily.
"""

__version__ = "0.1.0"

from .types import ActionCall, ActionResult, GameState, Observation

__all__ = [
    "ActionCall",
    "ActionResult",
    "GameState",
    "Observation",
    "__version__",
]
