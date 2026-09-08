"""Google Gemini provider (google-genai SDK).

Written against google-genai >= 0.3. The function-calling surface there has moved
more than the other two providers', so treat this file as the one to re-check
first if a run fails on startup: everything it touches is confined to
``_to_contents`` and ``complete``.
"""

from __future__ import annotations

import os

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage


def wire_declarations(tools: list[ToolSpec] | None) -> list[dict]:
    """Tool specs -> Gemini function declarations."""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in (tools or [])
    ]


def wire_contents(messages: list[Msg]) -> list[dict]:
    """Neutral messages -> Gemini `contents`.

    Gemini uses the roles "user" and "model", and carries tool results as
    functionResponse parts on a user turn.
    """
    contents: list[dict] = []
    for msg in messages:
        if msg.role == "assistant":
            parts: list[dict] = []
            if msg.text:
                parts.append({"text": msg.text})
            for call in msg.tool_calls:
                parts.append({"function_call": {"name": call.name, "args": call.arguments}})
            if parts:
                contents.append({"role": "model", "parts": parts})
        else:
            parts = [
                {
                    "function_response": {
                        "name": result.name or result.call_id,
                        "response": {"result": result.content},
                    }
                }
                for result in msg.tool_results
            ]
            if msg.text:
                parts.append({"text": msg.text})
            if parts:
                contents.append({"role": "user", "parts": parts})
    return contents


class GoogleClient(LLMClient):
    name = "google"

    def __init__(
        self,
        model: str = "gemini-2.5-pro",
        max_tokens: int = 16000,
        effort: str = "high",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        cache_prefix: bool = True,
    ):
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[google]'") from exc

        self.genai = genai
        self.client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _to_contents(self, messages: list[Msg]) -> list[dict]:
        return wire_contents(messages)

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        declarations = wire_declarations(tools)
        config: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens or self.max_tokens,
        }
        if declarations:
            config["tools"] = [{"function_declarations": declarations}]
        if self.temperature is not None:
            config["temperature"] = self.temperature

        response = self.client.models.generate_content(
            model=self.model, contents=self._to_contents(messages), config=config
        )

        calls = [
            ToolCall(id=f"{fc.name}-{i}", name=fc.name, arguments=dict(fc.args or {}))
            for i, fc in enumerate(getattr(response, "function_calls", None) or [])
        ]
        meta = getattr(response, "usage_metadata", None)
        usage = Usage(
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            cached_input_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
        )
        return LLMResponse(
            text=getattr(response, "text", "") or "",
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=usage,
            model=self.model,
            raw=response,
        )
