"""Anthropic provider (Claude).

Notes that matter for cost on a long campaign:

* the system prompt and the tool block are marked cacheable, so the stable
  prefix is billed at cache-read rates from the second call onward -- keep
  volatile text (the situation brief) in the last user message, never in
  ``system``;
* adaptive thinking with an ``effort`` setting replaces the old fixed thinking
  budget; ``low`` is usually right for routine turns and ``high`` for war;
* streaming is used unconditionally so a large ``max_tokens`` cannot trip an
  HTTP timeout.
"""

from __future__ import annotations

import os

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage


def wire_tools(tools: list[ToolSpec] | None, cache_prefix: bool = True) -> list[dict]:
    """Tool specs -> Anthropic `tools`. The last entry carries the cache breakpoint."""
    if not tools:
        return []
    payload = [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]
    if cache_prefix:
        payload[-1] = {**payload[-1], "cache_control": {"type": "ephemeral"}}
    return payload


def wire_messages(messages: list[Msg]) -> list[dict]:
    """Neutral messages -> Anthropic `messages`.

    Tool results are `tool_result` blocks on a *user* message, and they come
    before any new user text so the model reads results then instructions.
    """
    wire: list[dict] = []
    for msg in messages:
        if msg.role == "assistant":
            content: list[dict] = []
            if msg.text:
                content.append({"type": "text", "text": msg.text})
            for call in msg.tool_calls:
                content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            if content:
                wire.append({"role": "assistant", "content": content})
        else:
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": result.content,
                    **({"is_error": True} if result.is_error else {}),
                }
                for result in msg.tool_results
            ]
            if msg.text:
                content.append({"type": "text", "text": msg.text})
            if content:
                wire.append({"role": "user", "content": content})
    return wire


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        effort: str = "high",
        api_key: str | None = None,
        base_url: str | None = None,
        cache_prefix: bool = True,
        temperature: float | None = None,
    ):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[anthropic]'") from exc

        kwargs = {}
        if api_key or os.environ.get("ANTHROPIC_API_KEY"):
            kwargs["api_key"] = api_key or os.environ["ANTHROPIC_API_KEY"]
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.cache_prefix = cache_prefix

    def _tools(self, tools: list[ToolSpec] | None) -> list[dict]:
        return wire_tools(tools, self.cache_prefix)

    def _messages(self, messages: list[Msg]) -> list[dict]:
        return wire_messages(messages)

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        system_blocks = [{"type": "text", "text": system}]
        if self.cache_prefix:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system_blocks,
            messages=self._messages(messages),
            tools=self._tools(tools),
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        ) as stream:
            message = stream.get_final_message()

        text = "".join(b.text for b in message.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in message.content
            if b.type == "tool_use"
        ]
        usage = Usage(
            input_tokens=getattr(message.usage, "input_tokens", 0),
            output_tokens=getattr(message.usage, "output_tokens", 0),
            cached_input_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        )
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop_reason=message.stop_reason or "end_turn",
            usage=usage,
            model=self.model,
            raw=message,
        )
