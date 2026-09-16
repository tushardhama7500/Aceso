"""LLMService: the single seam between the Agent and any LLM provider.

Responsibilities that live HERE (never in the Agent):
  - provider selection / construction
  - bounded retry + fallback (primary -> fallback, never endless)
  - structured-output parsing/validation (Pydantic) with one corrective retry
  - per-call observability metadata (provider, model, tokens, TTFT, latency,
    fallback_used) and structured logging

The Agent only ever calls `generate_structured` / `generate_with_tools` on an
`LLMService` instance and never imports a provider module directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models.ids import new_id
from app.services.llm.base import LLMMessage, LLMProvider, LLMResult, ProviderError, Role, ToolDefinition, Usage
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.openrouter_provider import OpenRouterProvider

logger = logging.getLogger("aceso.llm")

T = TypeVar("T", bound=BaseModel)


class LLMCallMeta(BaseModel):
    request_id: str
    provider: str
    model: str
    usage: Usage
    ttft_ms: Optional[float] = None
    latency_ms: float
    fallback_used: bool = False
    fallback_from: Optional[str] = None


class LLMUnavailableError(Exception):
    """Every configured provider failed for this call."""


class LLMOutputError(Exception):
    """Provider(s) responded, but structured output never validated."""


def build_provider(name: str, settings: Settings) -> LLMProvider:
    if name == "groq":
        return GroqProvider(api_key=settings.groq_api_key, model=settings.groq_model)
    if name == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
    if name == "openrouter":
        return OpenRouterProvider(api_key=settings.openrouter_api_key, model=settings.openrouter_models[0])
    if name == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
    raise ValueError(f"Unknown LLM provider: {name}")


def build_extra_providers(name: str, settings: Settings) -> list[LLMProvider]:
    """Additional model variants for the same provider, tried in order after
    the first configured model fails. Only OpenRouter supports a
    comma-separated model list today (OPENROUTER_MODEL=model-a,model-b)."""
    if name == "openrouter":
        return [
            OpenRouterProvider(api_key=settings.openrouter_api_key, model=model)
            for model in settings.openrouter_models[1:]
        ]
    return []


async def _backoff_sleep(seconds: float) -> None:
    """Isolated so tests can mock just the retry backoff without touching
    `asyncio.sleep` globally, which providers/fakes may also rely on (e.g.
    to simulate a hang for timeout tests)."""
    await asyncio.sleep(seconds)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class LLMService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.primary_name = settings.llm_provider
        # Ordered list of fallback provider names, e.g. ["gemini", "openrouter"]
        # — tried in order after the primary. Deduplicated and never
        # includes the primary itself, preserving configured order.
        seen = {self.primary_name}
        self.fallback_names: list[str] = []
        for name in settings.fallback_provider_names:
            if name not in seen:
                seen.add(name)
                self.fallback_names.append(name)

        self._providers: dict[str, LLMProvider] = {}
        self._extra_providers: dict[str, list[LLMProvider]] = {}
        for name in {self.primary_name, *self.fallback_names}:
            self._providers[name] = build_provider(name, settings)
            self._extra_providers[name] = build_extra_providers(name, settings)

    def _candidates(self, provider_name: str) -> list[LLMProvider]:
        first = self._providers.get(provider_name)
        if first is None:
            return []
        # getattr guards test doubles that construct LLMService via
        # __new__ and only ever set `_providers` directly.
        extra = getattr(self, "_extra_providers", {}).get(provider_name, [])
        return [first, *extra]

    # ------------------------------------------------------------------
    # Core: bounded retry + fallback, applied uniformly to any provider method
    # ------------------------------------------------------------------
    async def _call_with_fallback(self, method: str, *args, **kwargs) -> tuple[LLMResult, LLMCallMeta]:
        request_id = new_id("req")
        plan = [(self.primary_name, False)] + [(name, True) for name in self.fallback_names]

        timeout = self.settings.llm_timeout_seconds
        backoff = self.settings.llm_retry_backoff_seconds

        last_error: Optional[Exception] = None
        for provider_name, is_fallback in plan:
            candidates = self._candidates(provider_name)
            # One retry on primary, single shot on fallback -> bounded latency.
            attempts = 1 if is_fallback else 2

            for provider in candidates:
                for attempt in range(attempts):
                    start = time.perf_counter()
                    retryable = True
                    try:
                        result: LLMResult = await asyncio.wait_for(
                            getattr(provider, method)(*args, **kwargs), timeout=timeout
                        )
                    except asyncio.TimeoutError:
                        last_error = ProviderError(f"{provider_name}/{provider.model} timed out after {timeout}s")
                        retryable = True
                    except ProviderError as e:
                        last_error = e
                        retryable = e.retryable
                    else:
                        latency_ms = (time.perf_counter() - start) * 1000
                        meta = LLMCallMeta(
                            request_id=request_id,
                            provider=provider_name,
                            model=provider.model,
                            usage=result.usage,
                            ttft_ms=result.ttft_ms,
                            latency_ms=latency_ms,
                            fallback_used=is_fallback,
                            fallback_from=self.primary_name if is_fallback else None,
                        )
                        logger.info("llm_call %s", json.dumps(meta.model_dump()))
                        return result, meta

                    logger.warning(
                        "llm_call_failed provider=%s model=%s attempt=%d/%d retryable=%s error=%s",
                        provider_name,
                        provider.model,
                        attempt + 1,
                        attempts,
                        retryable,
                        last_error,
                    )
                    if retryable and attempt + 1 < attempts:
                        await _backoff_sleep(backoff)
                        continue
                    break  # exhausted or non-retryable -> next model/provider, no wasted wait

        raise LLMUnavailableError(f"All LLM providers failed: {last_error}")

    def _defaults(self, temperature: Optional[float], max_tokens: Optional[int], frequency_penalty: Optional[float]):
        return (
            self.settings.llm_temperature if temperature is None else temperature,
            self.settings.llm_max_tokens if max_tokens is None else max_tokens,
            self.settings.llm_frequency_penalty if frequency_penalty is None else frequency_penalty,
        )

    # ------------------------------------------------------------------
    # Plain generation
    # ------------------------------------------------------------------
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
    ) -> tuple[LLMResult, LLMCallMeta]:
        t, mt, fp = self._defaults(temperature, max_tokens, frequency_penalty)
        return await self._call_with_fallback("generate", messages, temperature=t, max_tokens=mt, frequency_penalty=fp)

    # ------------------------------------------------------------------
    # Structured output (issue extraction, department reasoning, summaries)
    # ------------------------------------------------------------------
    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[T],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> tuple[T, LLMCallMeta]:
        t, mt, _ = self._defaults(temperature, max_tokens, None)
        schema_json = schema.model_json_schema()
        instruction = LLMMessage(
            role=Role.SYSTEM,
            content=(
                "Respond with ONLY a single valid JSON object matching this JSON schema. "
                "No markdown code fences, no commentary, no text outside the JSON object.\n\n"
                f"JSON schema:\n{json.dumps(schema_json)}"
            ),
        )
        working_messages = [*messages, instruction]

        result, meta = await self._call_with_fallback(
            "generate", working_messages, temperature=t, max_tokens=mt, frequency_penalty=0.0, response_schema=schema_json
        )
        parsed = self._try_parse(result.content, schema)
        if parsed is not None:
            return parsed, meta

        # One bounded corrective retry.
        correction = LLMMessage(
            role=Role.USER,
            content=(
                "Your previous response was not valid JSON matching the required schema. "
                "Reply again with ONLY the corrected JSON object, nothing else."
            ),
        )
        retry_messages = [*working_messages, LLMMessage(role=Role.ASSISTANT, content=result.content or ""), correction]
        result2, meta2 = await self._call_with_fallback(
            "generate", retry_messages, temperature=t, max_tokens=mt, frequency_penalty=0.0, response_schema=schema_json
        )
        parsed2 = self._try_parse(result2.content, schema)
        if parsed2 is not None:
            return parsed2, meta2

        raise LLMOutputError("Model did not return valid structured output after one corrective retry")

    @staticmethod
    def _try_parse(content: Optional[str], schema: type[T]) -> Optional[T]:
        if not content:
            return None
        try:
            data = json.loads(_strip_json_fence(content))
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            return None

    # ------------------------------------------------------------------
    # Tool calling (create_appointment)
    # ------------------------------------------------------------------
    async def generate_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        *,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> tuple[LLMResult, LLMCallMeta]:
        t, mt, _ = self._defaults(temperature, max_tokens, None)
        return await self._call_with_fallback(
            "generate_with_tools", messages, tools, tool_choice=tool_choice, temperature=t, max_tokens=mt
        )
