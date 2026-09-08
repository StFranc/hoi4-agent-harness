"""Provider-neutral LLM types.

The harness speaks this dialect and nothing else. Each provider module maps it
onto a vendor wire format, which keeps two things true: swapping models is a
config change, and a local model behind an OpenAI-compatible endpoint is a
first-class citizen rather than an afterthought.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ...actions.registry import ToolSpec


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    name: str = ""


@dataclass
class Msg:
    """One turn in the conversation.

    ``tool_results`` ride on a user-role message; providers that model tool
    results as their own role (OpenAI, Gemini) expand them at send time.
    """

    role: str                       # user | assistant
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"   # end_turn | tool_use | max_tokens | refusal | error
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    raw: Any = field(default=None, repr=False)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(abc.ABC):
    """What the agent loop needs from a model. Nothing more."""

    name: str = "llm"
    model: str = ""
    #: The provider enforces a tool's JSON Schema at decode time (structured
    #: outputs), so a malformed call cannot be produced in the first place.
    #: Defaulting False on purpose: a wrong True sends a field the endpoint may
    #: reject and fail every call on. It never changes what the harness does
    #: with the calls it gets back -- validate.py runs either way.
    supports_strict_tools: bool = False

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def close(self) -> None:
        """Release clients/sessions. Default: nothing to do."""
