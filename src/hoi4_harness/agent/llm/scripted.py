"""A deterministic stand-in for a model.

Two modes:

* ``ScriptedClient(script=[...])`` replays a fixed list of responses -- what the
  tests use to pin loop behaviour;
* ``ScriptedClient()`` with no script runs a tiny keyword policy over the brief,
  which is enough to drive a complete offline campaign against the mock adapter.

The point is that every part of the harness except the model itself can be
exercised for free, in CI, with no API key.
"""

from __future__ import annotations

import itertools

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage


class ScriptedClient(LLMClient):
    name = "scripted"

    def __init__(self, script: list[LLMResponse] | None = None, model: str = "scripted"):
        self.script = list(script or [])
        self.model = model
        self.calls: list[tuple[str, list[Msg]]] = []
        # Per-instance, so two runs of the same scenario produce byte-identical
        # transcripts. A module-level counter would leak state between runs and
        # quietly break the determinism the eval runner depends on.
        self._ids = itertools.count(1)

    def _call(self, name: str, **arguments) -> ToolCall:
        return ToolCall(id=f"scripted-{next(self._ids)}", name=name, arguments=arguments)

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        self.calls.append((system, list(messages)))
        if self.script:
            return self.script.pop(0)
        return self._policy(messages, tools)

    def _policy(self, messages: list[Msg], tools: list[ToolSpec] | None) -> LLMResponse:
        brief = next((m.text for m in reversed(messages) if m.role == "user" and m.text), "")
        available = {t.name for t in (tools or [])}
        calls: list[ToolCall] = []

        if "NONE SELECTED" in brief and "set_national_focus" in available:
            calls.append(self._call("set_national_focus", focus_id="industrial_effort"))
        if "SLOT(S) FREE" in brief and "start_research" in available:
            calls.append(self._call("start_research", technology="construction1"))
        if "QUEUE EMPTY" in brief and "queue_construction" in available:
            calls.append(self._call("queue_construction", building="civilian_factory",
                               state="capital", count=2))
        if "IDLE" in brief and "set_production" in available:
            calls.append(self._call("set_production", equipment="infantry_equipment_1", factories=5))
        if not calls and "advance_time" in available:
            calls.append(self._call("advance_time", days=7, reason="nothing worth an action this week"))

        return LLMResponse(
            text="" if calls else "Holding.",
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=Usage(input_tokens=len(brief) // 4, output_tokens=24),
            model=self.model,
        )
