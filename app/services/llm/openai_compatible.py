"""Shared implementation for any provider that speaks the OpenAI chat
completions wire format — OpenAI itself, and OpenRouter (which is
OpenAI-API-compatible). Only construction (base_url, headers) differs.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Optional

import openai
from openai import AsyncOpenAI

from app.services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResult,
    ProviderError,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)


def _is_retryable(exc: Exception) -> bool:
    """429 (rate limit/quota) and 401/403 (auth) will fail identically on a
    retry, so they're not worth one. Timeouts, connection errors, and 5xx
    are transient — worth a quick retry or the next candidate."""
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, (openai.RateLimitError, openai.AuthenticationError, openai.PermissionDeniedError)):
        return False
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500
    return True


def _to_openai_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == Role.ASSISTANT and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == Role.TOOL:
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            out.append({"role": m.role.value, "content": m.content})
    return out


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, name: str, api_key: str, model: str, base_url: Optional[str] = None):
        self.name = name
        self.model = model
        # max_retries=0: LLMService already owns retry/backoff/fallback.
        # The SDK's own default retry (2 attempts, sleeping for however long
        # the server's Retry-After says — Groq/OpenRouter can ask for 10-20s
        # on a 429) runs INSIDE our asyncio.wait_for(LLM_TIMEOUT_SECONDS),
        # so it can silently eat the whole timeout budget and turn a fast,
        # correctly-classified 429 (retryable=False, instant fallback) into
        # a slow, misclassified timeout (retryable=True) instead.
        self.client = AsyncOpenAI(api_key=api_key or "unset", base_url=base_url, max_retries=0)

    def _require_choice(self, resp):
        # Some providers (notably OpenRouter, on overloaded/rate-limited free
        # models) return HTTP 200 with an `error` body instead of `choices`.
        # The SDK doesn't raise for that, so check explicitly.
        if not getattr(resp, "choices", None):
            error = getattr(resp, "error", None)
            detail = error.get("message") if isinstance(error, dict) else error
            raise ProviderError(f"{self.name} returned no choices: {detail or resp}", retryable=True)
        return resp.choices[0]

    def _usage(self, resp_usage) -> Usage:
        if resp_usage is None:
            return Usage()
        cached = None
        details = getattr(resp_usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", None)
        return Usage(
            input_tokens=getattr(resp_usage, "prompt_tokens", None),
            output_tokens=getattr(resp_usage, "completion_tokens", None),
            cached_tokens=cached,
            total_tokens=getattr(resp_usage, "total_tokens", None),
        )

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        frequency_penalty: float = 0.0,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=_to_openai_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
        )
        try:
            if response_schema is not None:
                try:
                    resp = await self.client.chat.completions.create(
                        **kwargs, response_format={"type": "json_object"}
                    )
                except Exception:
                    # Some free/OpenRouter-routed models reject response_format.
                    # Our messages already carry JSON instructions in the prompt,
                    # so retry once without it rather than failing the whole call.
                    resp = await self.client.chat.completions.create(**kwargs)
            else:
                resp = await self.client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name} generate failed: {e}", retryable=_is_retryable(e)) from e

        choice = self._require_choice(resp)
        return LLMResult(
            content=choice.message.content,
            usage=self._usage(resp.usage),
            finish_reason=choice.finish_reason,
        )

    async def generate_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        *,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> LLMResult:
        oa_tool_choice = "required" if tool_choice == "required" else "auto"
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=_to_openai_messages(messages),
                tools=[
                    {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                    for t in tools
                ],
                tool_choice=oa_tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name} generate_with_tools failed: {e}", retryable=_is_retryable(e)) from e

        choice = self._require_choice(resp)
        tool_calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMResult(
            content=choice.message.content,
            tool_calls=tool_calls,
            usage=self._usage(resp.usage),
            finish_reason=choice.finish_reason,
        )

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=_to_openai_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name} generate_stream failed: {e}", retryable=_is_retryable(e)) from e

        async for chunk in stream:
            if not chunk.choices:
                if chunk.usage:
                    yield StreamChunk(delta="", is_final=True, usage=self._usage(chunk.usage))
                continue
            choice = chunk.choices[0]
            delta = choice.delta.content or ""
            finished = choice.finish_reason is not None
            yield StreamChunk(delta=delta, is_final=finished, finish_reason=choice.finish_reason)
