"""Fake LLMProvider implementations for tests. No network calls are ever made."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Optional

from app.services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResult,
    ProviderError,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)


class Hang:
    """Queue this instead of an LLMResult/Exception to simulate a provider
    that never responds in time — the fake actually awaits `seconds` before
    returning ok, so a real (small) LLM_TIMEOUT_SECONDS genuinely fires."""

    def __init__(self, seconds: float):
        self.seconds = seconds


class FakeProvider(LLMProvider):
    """A scriptable fake provider.

    `responses` is a queue of LLMResult / Exception / Hang values popped in
    order, one per call to generate/generate_with_tools. If exhausted,
    repeats the last entry.
    """

    def __init__(self, name: str = "fake", model: str = "fake-model", responses: Optional[list] = None):
        self.name = name
        self.model = model
        self.responses = list(responses or [LLMResult(content="ok")])
        self.calls: list[str] = []

    async def _next(self):
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Hang):
            await asyncio.sleep(item.seconds)
            return LLMResult(content="ok")
        if isinstance(item, Exception):
            raise item
        return item

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        frequency_penalty: float = 0.0,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> LLMResult:
        self.calls.append("generate")
        return await self._next()

    async def generate_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        *,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> LLMResult:
        self.calls.append("generate_with_tools")
        return await self._next()

    async def generate_stream(self, messages, *, temperature, max_tokens) -> AsyncIterator[StreamChunk]:
        self.calls.append("generate_stream")
        yield StreamChunk(delta="ok", is_final=True)
