"""Provider conformance against live keys (#22). Opt-in, and cheap.

These tests spend real money, so two things must both be true before any runs:
``HOI4_LIVE_TESTS=1`` is set, and the provider's key is in the environment. A
developer with keys in their shell who types ``pytest`` is not billed; a fork
with no secrets sees skips, not failures.

Each provider is checked with the model named by ``HOI4_LIVE_<PROVIDER>_MODEL``,
or the harness's default for that provider.
"""

from __future__ import annotations

import os

import pytest

from hoi4_harness.config import DEFAULT_MODELS, LLMConfig

KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}
SDKS = {"anthropic": "anthropic", "openai": "openai", "google": "google.genai"}


def _skip_reason(provider: str) -> str | None:
    if os.environ.get("HOI4_LIVE_TESTS") != "1":
        return "live provider tests are opt-in: set HOI4_LIVE_TESTS=1 (they cost money)"
    if not os.environ.get(KEYS[provider]):
        return f"{KEYS[provider]} is not set"
    try:
        __import__(SDKS[provider])
    except ImportError:
        return f"the {provider} SDK is not installed (pip install 'hoi4-agent-harness[{provider}]')"
    return None


@pytest.fixture(params=sorted(KEYS))
def live(request):
    """(provider, client) for every provider that is configured; the rest skip."""
    provider = request.param
    reason = _skip_reason(provider)
    if reason:
        pytest.skip(reason)
    from hoi4_harness.agent.llm import build_llm

    model = os.environ.get(f"HOI4_LIVE_{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]
    # A small output budget and low effort: every check here is one short turn.
    client = build_llm(LLMConfig(provider=provider, model=model, max_tokens=2048, effort="low"))
    yield provider, client
    client.close()
