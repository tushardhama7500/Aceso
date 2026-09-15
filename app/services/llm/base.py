"""Provider-agnostic LLM abstraction.

The Agent (app/services/agent.py) only ever talks to `LLMService`, which in
turn talks to `LLMProvider` implementations through this interface. No
provider-specific code should ever leak upward into the Agent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: Role
    content: str = ""
    # Set on an ASSISTANT message that represents a prior turn where the
    # model invoked one or more tools, so providers can reconstruct history.
    tool_calls: Optional[list[ToolCall]] = None
    # Set on a TOOL message: which call this result answers, and its name.
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema object


class Usage(BaseModel):
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class LLMResult(BaseModel):
    content: Optional[str] = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    ttft_ms: Optional[float] = None
    finish_reason: Optional[str] = None


class StreamChunk(BaseModel):
    delta: str = ""
    is_final: bool = False
    usage: Optional[Usage] = None
    finish_reason: Optional[str] = None


class ProviderError(Exception):
    """Raised by a provider on any failure. LLMService treats this as
    retryable/fallback-triggering — providers should not raise raw SDK
    exceptions upward."""


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        frequency_penalty: float = 0.0,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> LLMResult:
        """Plain-text (or JSON-mode, best-effort) generation."""

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        *,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> LLMResult:
        """Generation with function/tool calling enabled."""

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        """Streamed generation, used to measure true TTFT."""
