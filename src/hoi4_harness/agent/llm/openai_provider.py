"""OpenAI and any OpenAI-compatible endpoint.

One class covers OpenAI itself plus everything that speaks the same protocol:
Ollama (http://localhost:11434/v1), vLLM, LM Studio, OpenRouter, Together, Groq,
DeepSeek. Point ``base_url`` at the server and set the model name it serves.

OpenAI proper with a structured-outputs model also gets strict tool schemas
(``supports_strict_tools``), where the server enforces a tool's schema at decode
time. An endpoint that refuses the field is downgraded to unmarked tools
mid-run, and validate.py runs regardless -- nothing depends on the promise.

Local models are the reason the harness caps tool count and keeps schemas flat:
smaller models handle a short enum-heavy tool list far better than a long one.
"""

from __future__ import annotations

import json
import os

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage

# Reasoning-capable families that accept `reasoning_effort`. Unknown models are
# tried without it; a server that rejects the field would otherwise fail the run.
_REASONING_HINTS = ("gpt-5", "o1", "o3", "o4", "deepseek-r", "qwq")

# Structured-outputs families that accept `strict` on a function tool. Same
# style of guess as _REASONING_HINTS, and it needs OpenAI's own endpoint:
# aggregators and local servers are free to ignore or reject the field, so they
# are never told. A model that turns out to refuse it anyway is downgraded at
# runtime (see complete), and the local validator was guarding it regardless.
_STRICT_HINTS = ("gpt-5", "gpt-4.1", "gpt-4o", "o1", "o3", "o4")

# The JSON Schema keywords structured outputs documents as supported. Anything
# outside this set means the schema cannot be marked strict, even if it would
# otherwise pass the structural rules below.
_STRICT_KEYWORDS = frozenset({
    "type", "description", "enum", "properties", "required", "additionalProperties",
    "items", "minimum", "maximum", "minLength", "maxLength", "pattern", "format",
    "anyOf", "default",
})


def strict_supported(model: str, base_url: str | None) -> bool:
    """Whether this endpoint and model may be sent ``strict`` tool schemas.

    Only OpenAI proper with a structured-outputs model qualifies. Everything
    behind ``base_url`` -- Ollama, vLLM, OpenRouter, anything else that speaks
    the protocol -- defaults to False because whether it honours the field is
    endpoint-specific, and a wrong True fails every call on.
    """
    return base_url is None and any(hint in model.lower() for hint in _STRICT_HINTS)


def strict_compatible(schema: dict) -> bool:
    """Whether structured outputs can enforce this schema as written.

    Two hard rules sit on top of the keyword subset: every object must close
    with ``additionalProperties: False`` and every property must be listed in
    ``required`` -- strict mode has no optional fields. Several catalog actions
    have them (``count``, ``target``, ``detail`` ...), so their tools go out
    unmarked and keep the local validator as their guard rather than failing
    the whole request.
    """
    if not isinstance(schema, dict) or not _STRICT_KEYWORDS.issuperset(schema):
        return False
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            return False
        if set(schema.get("required", ())) != set(props):
            return False
        if not all(strict_compatible(sub) for sub in props.values()):
            return False
    if "items" in schema and not strict_compatible(schema["items"]):
        return False
    if "anyOf" in schema and not all(strict_compatible(b) for b in schema["anyOf"]):
        return False
    return True


def wire_tools(tools: list[ToolSpec] | None, strict: bool = False) -> list[dict] | None:
    """Tool specs -> OpenAI `tools`.

    With ``strict``, tools whose schema structured outputs can enforce get
    ``strict: True``, so the server constrains decoding and a malformed call
    becomes impossible instead of a rejected round trip. Schemas strict mode
    cannot express go out unmarked: marking one fails the whole request, not
    just that tool.
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                **({"strict": True} if strict and strict_compatible(t.parameters) else {}),
            },
        }
        for t in tools
    ]


def wire_messages(system: str, messages: list[Msg]) -> list[dict]:
    """Neutral messages -> OpenAI `messages`.

    Tool results are their own role here, and must come *before* any new user
    text so the assistant reads results then instructions.
    """
    wire: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.text or None}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in msg.tool_calls
                ]
            wire.append(entry)
        else:
            for result in msg.tool_results:
                wire.append(
                    {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
                )
            if msg.text:
                wire.append({"role": "user", "content": msg.text})
    return wire


class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-5",
        max_tokens: int = 16000,
        effort: str = "high",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        cache_prefix: bool = True,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[openai]'") from exc

        self.client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY") or "not-needed-for-local",
            base_url=base_url,
        )
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.temperature = temperature
        self._send_effort = any(hint in model.lower() for hint in _REASONING_HINTS)
        self.supports_strict_tools = strict_supported(model, base_url)

    def _tools(self, tools: list[ToolSpec] | None, strict: bool = False) -> list[dict] | None:
        return wire_tools(tools, strict)

    def _messages(self, system: str, messages: list[Msg]) -> list[dict]:
        return wire_messages(system, messages)

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        strict = self.supports_strict_tools
        kwargs: dict = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "max_completion_tokens": max_tokens or self.max_tokens,
        }
        wire_tools = self._tools(tools, strict)
        if wire_tools:
            kwargs["tools"] = wire_tools
            kwargs["tool_choice"] = "auto"
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self._send_effort and self.effort:
            kwargs["reasoning_effort"] = self.effort

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - one retry without optional fields
            if strict and wire_tools and "strict" in str(exc).lower():
                # Strict is an optimisation, never a requirement: an endpoint
                # that refuses the field gets the unmarked tools and this call
                # goes through. The downgrade sticks, so one refusal does not
                # cost a round trip on every later call of a long run.
                self.supports_strict_tools = False
                kwargs["tools"] = self._tools(tools)
                response = self.client.chat.completions.create(**kwargs)
            else:
                if "reasoning_effort" not in kwargs and "max_completion_tokens" not in str(exc):
                    raise
                kwargs.pop("reasoning_effort", None)
                if "max_completion_tokens" in str(exc):
                    kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        calls = []
        for call in choice.message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparsed": call.function.arguments}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

        usage = Usage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
            cached_input_tokens=getattr(
                getattr(response.usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            or 0,
        )
        return LLMResponse(
            text=choice.message.content or "",
            tool_calls=calls,
            stop_reason="tool_use" if calls else (choice.finish_reason or "end_turn"),
            usage=usage,
            model=self.model,
            raw=response,
        )
