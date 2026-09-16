"""Tests for the LLM abstraction: fallback behavior and structured-output
validation. No real provider SDKs are invoked — everything uses FakeProvider."""
from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.services.llm.base import LLMMessage, LLMResult, ProviderError, Role, ToolCall, Usage
from app.services.llm.service import LLMOutputError, LLMService, LLMUnavailableError
from tests.fakes import FakeProvider


def make_service(
    primary: FakeProvider,
    fallback: FakeProvider | list[FakeProvider] | None = None,
    *,
    timeout_seconds: float = 6.0,
    backoff_seconds: float = 0.5,
) -> LLMService:
    """Build an LLMService test double. `fallback` accepts a single
    FakeProvider (kept for existing single-fallback tests) or a list, tried
    in that order, to exercise a multi-provider fallback chain."""
    fallbacks = fallback if isinstance(fallback, list) else ([fallback] if fallback else [])
    settings = Settings(
        llm_provider=primary.name,
        llm_fallback_providers=",".join(fb.name for fb in fallbacks),
        llm_timeout_seconds=timeout_seconds,
        llm_retry_backoff_seconds=backoff_seconds,
    )
    svc = LLMService.__new__(LLMService)
    svc.settings = settings
    svc.primary_name = primary.name
    svc.fallback_names = [fb.name for fb in fallbacks]
    svc._providers = {primary.name: primary}
    for fb in fallbacks:
        svc._providers[fb.name] = fb
    return svc


@pytest.mark.asyncio
async def test_generate_uses_primary_when_healthy():
    primary = FakeProvider(name="gemini", responses=[LLMResult(content="hello")])
    svc = make_service(primary)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "hello"
    assert meta.provider == "gemini"
    assert meta.fallback_used is False


@pytest.mark.asyncio
async def test_falls_back_when_primary_fails():
    primary = FakeProvider(name="gemini", responses=[ProviderError("rate limited"), ProviderError("rate limited")])
    fallback = FakeProvider(name="openrouter", responses=[LLMResult(content="fallback reply")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "fallback reply"
    assert meta.provider == "openrouter"
    assert meta.fallback_used is True
    assert meta.fallback_from == "gemini"


@pytest.mark.asyncio
async def test_bounded_retry_does_not_retry_forever():
    primary = FakeProvider(name="gemini", responses=[ProviderError("down")])
    svc = make_service(primary)  # no fallback configured

    with pytest.raises(LLMUnavailableError):
        await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    # exactly the bounded number of attempts (2) on primary, no infinite loop
    assert primary.calls.count("generate") <= 2


class _Extraction(BaseModel):
    department: str
    confidence: float


@pytest.mark.asyncio
async def test_generate_structured_parses_valid_json():
    primary = FakeProvider(name="gemini", responses=[LLMResult(content='{"department": "ENT", "confidence": 0.9}')])
    svc = make_service(primary)

    parsed, _ = await svc.generate_structured([LLMMessage(role=Role.USER, content="hi")], _Extraction)

    assert parsed.department == "ENT"
    assert parsed.confidence == 0.9


@pytest.mark.asyncio
async def test_generate_structured_retries_once_on_bad_json_then_succeeds():
    primary = FakeProvider(
        name="gemini",
        responses=[
            LLMResult(content="not json at all"),
            LLMResult(content='{"department": "Neurology", "confidence": 0.5}'),
        ],
    )
    svc = make_service(primary)

    parsed, _ = await svc.generate_structured([LLMMessage(role=Role.USER, content="hi")], _Extraction)

    assert parsed.department == "Neurology"
    assert primary.calls.count("generate") == 2


@pytest.mark.asyncio
async def test_generate_structured_raises_after_exhausting_retry():
    primary = FakeProvider(name="gemini", responses=[LLMResult(content="still not json"), LLMResult(content="nope")])
    svc = make_service(primary)

    with pytest.raises(LLMOutputError):
        await svc.generate_structured([LLMMessage(role=Role.USER, content="hi")], _Extraction)


@pytest.mark.asyncio
async def test_generate_with_tools_returns_tool_calls():
    primary = FakeProvider(
        name="gemini",
        responses=[
            LLMResult(
                tool_calls=[ToolCall(id="1", name="create_appointment", arguments={"department": "ENT"})],
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        ],
    )
    svc = make_service(primary)

    from app.services.llm.base import ToolDefinition

    tool = ToolDefinition(name="create_appointment", description="Book an appointment", parameters={"type": "object"})
    result, meta = await svc.generate_with_tools([LLMMessage(role=Role.USER, content="book it")], [tool])

    assert result.tool_calls[0].name == "create_appointment"
    assert meta.usage.total_tokens == 15


# ----------------------------------------------------------------------
# Latency-aware retry/fallback: non-retryable errors skip straight to
# fallback, transient errors get one small-backoff retry, timeouts are
# bounded by LLM_TIMEOUT_SECONDS, and successful calls never sleep.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_429_skips_retry_and_falls_back_immediately(mock_llm_retry_sleep):
    primary = FakeProvider(name="gemini", responses=[ProviderError("quota exceeded", retryable=False)])
    fallback = FakeProvider(name="openrouter", responses=[LLMResult(content="fallback reply")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "fallback reply"
    assert meta.fallback_used is True
    # Exactly one attempt on primary — no wasted retry on a doomed 429.
    assert primary.calls.count("generate") == 1
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["401 unauthorized", "403 forbidden"])
async def test_immediate_401_403_skips_retry_and_falls_back_immediately(message, mock_llm_retry_sleep):
    primary = FakeProvider(name="gemini", responses=[ProviderError(message, retryable=False)])
    fallback = FakeProvider(name="openrouter", responses=[LLMResult(content="fallback reply")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "fallback reply"
    assert primary.calls.count("generate") == 1
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_is_bounded_then_retries_then_falls_back(mock_llm_retry_sleep):
    from tests.fakes import Hang

    primary = FakeProvider(name="gemini", responses=[Hang(seconds=5), Hang(seconds=5)])
    fallback = FakeProvider(name="openrouter", responses=[LLMResult(content="fallback reply")])
    svc = make_service(primary, fallback, timeout_seconds=0.05, backoff_seconds=0.01)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "fallback reply"
    assert meta.fallback_used is True
    # Bounded: exactly the 2 allotted primary attempts, both timing out.
    assert primary.calls.count("generate") == 2
    # A retryable failure (timeout) got exactly one backoff sleep before the
    # second attempt.
    mock_llm_retry_sleep.assert_awaited_once_with(0.01)


@pytest.mark.asyncio
async def test_timeout_configuration_is_actually_respected():
    from tests.fakes import Hang

    primary = FakeProvider(name="gemini", responses=[Hang(seconds=5)])
    svc = make_service(primary, timeout_seconds=0.05, backoff_seconds=0.01)

    start = time.perf_counter()
    with pytest.raises(LLMUnavailableError):
        await svc.generate([LLMMessage(role=Role.USER, content="hi")])
    elapsed = time.perf_counter() - start

    # Two 0.05s timeouts plus a mocked-instant backoff must stay far below
    # the Hang's real 5s delay — proves LLM_TIMEOUT_SECONDS actually bounds
    # the wait rather than the call silently running to completion.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_5xx_retries_once_then_falls_back(mock_llm_retry_sleep):
    primary = FakeProvider(
        name="gemini",
        responses=[
            ProviderError("gemini 503 UNAVAILABLE", retryable=True),
            ProviderError("gemini 503 UNAVAILABLE", retryable=True),
        ],
    )
    fallback = FakeProvider(name="openrouter", responses=[LLMResult(content="fallback reply")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "fallback reply"
    assert primary.calls.count("generate") == 2
    mock_llm_retry_sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_successful_first_attempt_never_retries_or_sleeps(mock_llm_retry_sleep):
    primary = FakeProvider(name="gemini", responses=[LLMResult(content="hello")])
    svc = make_service(primary)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "hello"
    assert primary.calls.count("generate") == 1
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_openrouter_model_one_fails_model_two_succeeds(mock_llm_retry_sleep):
    model_one = FakeProvider(name="openrouter", model="model-a", responses=[ProviderError("overloaded", retryable=False)])
    model_two = FakeProvider(name="openrouter", model="model-b", responses=[LLMResult(content="from model b")])

    settings = Settings(llm_provider="openrouter", llm_fallback_providers="")
    svc = LLMService.__new__(LLMService)
    svc.settings = settings
    svc.primary_name = "openrouter"
    svc.fallback_names = []
    svc._providers = {"openrouter": model_one}
    svc._extra_providers = {"openrouter": [model_two]}

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from model b"
    assert meta.model == "model-b"
    assert model_one.calls.count("generate") == 1  # non-retryable -> no wasted retry
    assert model_two.calls.count("generate") == 1


@pytest.mark.asyncio
async def test_openrouter_model_list_is_wired_from_comma_separated_setting():
    from app.services.llm.service import LLMService as _LLMService

    settings = Settings(
        llm_provider="openrouter",
        llm_fallback_providers="",
        openrouter_api_key="k",
        openrouter_model="model-a,model-b,model-c",
    )
    svc = _LLMService(settings)

    assert svc._providers["openrouter"].model == "model-a"
    assert [p.model for p in svc._extra_providers["openrouter"]] == ["model-b", "model-c"]
