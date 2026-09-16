from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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


def _split_system_and_contents(messages: list[LLMMessage]) -> tuple[Optional[str], list[types.Content]]:
    system_parts: list[str] = []
    contents: list[types.Content] = []

    for m in messages:
        if m.role == Role.SYSTEM:
            system_parts.append(m.content)
        elif m.role == Role.ASSISTANT and m.tool_calls:
            parts = [types.Part.from_function_call(name=tc.name, args=tc.arguments) for tc in m.tool_calls]
            if m.content:
                parts.insert(0, types.Part.from_text(text=m.content))
            contents.append(types.Content(role="model", parts=parts))
        elif m.role == Role.TOOL:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=m.name or "tool", response={"result": m.content})],
                )
            )
        else:
            role = "model" if m.role == Role.ASSISTANT else "user"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m.content)]))

    system_instruction = "\n\n".join(system_parts) if system_parts else None

    if not contents and system_instruction:
        # Gemini requires at least one content turn — unlike OpenAI-compatible
        # APIs, a system-instruction-only message list is rejected outright.
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=system_instruction)])]
        system_instruction = None

    return system_instruction, contents


def _is_retryable(exc: Exception) -> bool:
    """429 (quota) and 401/403 (auth) will fail identically on a retry, so
    they're not worth one. 5xx and anything unrecognized (timeouts,
    connection errors) are treated as transient."""
    if isinstance(exc, genai_errors.APIError):
        code = exc.code or 0
        if code in (401, 403, 429):
            return False
        if 400 <= code < 500:
            return False
        return True
    return True


def _usage(resp) -> Usage:
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=meta.prompt_token_count,
        output_tokens=meta.candidates_token_count,
        cached_tokens=meta.cached_content_token_count,
        total_tokens=meta.total_token_count,
    )


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str, model: str):
        self.model = model
        self.client = genai.Client(api_key=api_key or "unset")

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        frequency_penalty: float = 0.0,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> LLMResult:
        system_instruction, contents = _split_system_and_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            response_mime_type="application/json" if response_schema else None,
        )
        try:
            resp = await self.client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"gemini generate failed: {e}", retryable=_is_retryable(e)) from e

        finish_reason = None
        if resp.candidates:
            finish_reason = str(resp.candidates[0].finish_reason) if resp.candidates[0].finish_reason else None

        return LLMResult(content=resp.text, usage=_usage(resp), finish_reason=finish_reason)

    async def generate_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        *,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> LLMResult:
        system_instruction, contents = _split_system_and_contents(messages)
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description, parameters=t.parameters) for t in tools
        ]
        mode = "ANY" if tool_choice == "required" else "AUTO"
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode)),
        )
        try:
            resp = await self.client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"gemini generate_with_tools failed: {e}", retryable=_is_retryable(e)) from e

        tool_calls = [
            ToolCall(id=f"call_{i}", name=fc.name, arguments=dict(fc.args or {}))
            for i, fc in enumerate(resp.function_calls or [])
        ]
        return LLMResult(content=resp.text if not tool_calls else None, tool_calls=tool_calls, usage=_usage(resp))

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        system_instruction, contents = _split_system_and_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction, temperature=temperature, max_output_tokens=max_tokens
        )
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=self.model, contents=contents, config=config
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"gemini generate_stream failed: {e}", retryable=_is_retryable(e)) from e

        last_usage: Optional[Usage] = None
        async for chunk in stream:
            last_usage = _usage(chunk)
            yield StreamChunk(delta=chunk.text or "", is_final=False)
        yield StreamChunk(delta="", is_final=True, usage=last_usage)
