"""Tests for Groq as Aceso's primary LLM provider.

Groq exposes an OpenAI-compatible API, so `GroqProvider` is a thin subclass
of `OpenAICompatibleProvider` (see app/services/llm/groq_provider.py) — no
new wire-format code, no Agent changes. These tests cover the provider
chain Groq -> Gemini -> OpenRouter introduced to support it: LLMService now
carries an ORDERED LIST of fallback providers (`fallback_names`) instead of
a single one, configured via LLM_FALLBACK_PROVIDERS=gemini,openrouter.

No real network calls are made — FakeProvider stands in for every provider,
and a real Settings/LLMService construction is used only to check wiring
(base_url, model, fallback order), never to hit Groq's actual API.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.llm.base import LLMMessage, LLMResult, ProviderError, Role, ToolCall, ToolDefinition, Usage
from app.services.llm.groq_provider import GROQ_BASE_URL, GroqProvider
from app.services.llm.service import LLMService, LLMUnavailableError
from tests.fakes import FakeProvider, Hang
from tests.test_llm_providers import make_service


def test_groq_provider_is_wired_as_an_openai_compatible_client():
    provider = GroqProvider(api_key="k", model="openai/gpt-oss-120b")

    assert provider.name == "groq"
    assert provider.model == "openai/gpt-oss-120b"
    assert str(provider.client.base_url).rstrip("/") == GROQ_BASE_URL


def test_llm_service_wires_groq_primary_gemini_openrouter_fallback_chain():
    settings = Settings(
        llm_provider="groq",
        llm_fallback_providers="gemini,openrouter",
        groq_api_key="k",
        groq_model="openai/gpt-oss-120b",
        gemini_api_key="k",
        gemini_model="gemini-3.6-flash",
        openrouter_api_key="k",
        openrouter_model="model-a,model-b",
    )
    svc = LLMService(settings)

    assert svc.primary_name == "groq"
    assert svc.fallback_names == ["gemini", "openrouter"]
    assert svc._providers["groq"].model == "openai/gpt-oss-120b"
    assert svc._providers["gemini"].model == "gemini-3.6-flash"
    assert svc._providers["openrouter"].model == "model-a"
    assert [p.model for p in svc._extra_providers["openrouter"]] == ["model-b"]


@pytest.mark.asyncio
async def test_groq_successful_request():
    primary = FakeProvider(name="groq", model="openai/gpt-oss-120b", responses=[LLMResult(content="hello")])
    svc = make_service(primary)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "hello"
    assert meta.provider == "groq"
    assert meta.model == "openai/gpt-oss-120b"
    assert meta.fallback_used is False


@pytest.mark.asyncio
async def test_groq_tool_calling():
    primary = FakeProvider(
        name="groq",
        model="openai/gpt-oss-120b",
        responses=[
            LLMResult(
                tool_calls=[ToolCall(id="1", name="create_appointment", arguments={"department": "ENT"})],
                usage=Usage(input_tokens=12, output_tokens=6, total_tokens=18),
            )
        ],
    )
    svc = make_service(primary)

    tool = ToolDefinition(name="create_appointment", description="Book an appointment", parameters={"type": "object"})
    result, meta = await svc.generate_with_tools([LLMMessage(role=Role.USER, content="book it")], [tool])

    assert result.tool_calls[0].name == "create_appointment"
    assert meta.provider == "groq"
    assert meta.usage.total_tokens == 18


@pytest.mark.asyncio
async def test_groq_structured_output():
    from pydantic import BaseModel

    class _Extraction(BaseModel):
        department: str
        confidence: float

    primary = FakeProvider(
        name="groq",
        model="openai/gpt-oss-120b",
        responses=[LLMResult(content='{"department": "ENT", "confidence": 0.9}')],
    )
    svc = make_service(primary)

    parsed, meta = await svc.generate_structured([LLMMessage(role=Role.USER, content="hi")], _Extraction)

    assert parsed.department == "ENT"
    assert meta.provider == "groq"


@pytest.mark.asyncio
async def test_groq_429_falls_back_to_gemini_immediately(mock_llm_retry_sleep):
    primary = FakeProvider(name="groq", responses=[ProviderError("429 quota exceeded", retryable=False)])
    fallback = FakeProvider(name="gemini", responses=[LLMResult(content="from gemini")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from gemini"
    assert meta.provider == "gemini"
    assert meta.fallback_used is True
    assert meta.fallback_from == "groq"
    assert primary.calls.count("generate") == 1  # non-retryable -> no wasted retry
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["401 unauthorized", "403 forbidden"])
async def test_groq_401_403_falls_back_to_gemini_immediately(message, mock_llm_retry_sleep):
    primary = FakeProvider(name="groq", responses=[ProviderError(message, retryable=False)])
    fallback = FakeProvider(name="gemini", responses=[LLMResult(content="from gemini")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from gemini"
    assert primary.calls.count("generate") == 1
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_groq_timeout_retries_once_then_falls_back_to_gemini(mock_llm_retry_sleep):
    primary = FakeProvider(name="groq", responses=[Hang(seconds=5), Hang(seconds=5)])
    fallback = FakeProvider(name="gemini", responses=[LLMResult(content="from gemini")])
    svc = make_service(primary, fallback, timeout_seconds=0.05, backoff_seconds=0.01)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from gemini"
    assert meta.fallback_used is True
    assert primary.calls.count("generate") == 2  # bounded retry, both timed out
    mock_llm_retry_sleep.assert_awaited_once_with(0.01)


@pytest.mark.asyncio
async def test_groq_5xx_retries_once_then_falls_back_to_gemini(mock_llm_retry_sleep):
    primary = FakeProvider(
        name="groq",
        responses=[
            ProviderError("groq 503 service unavailable", retryable=True),
            ProviderError("groq 503 service unavailable", retryable=True),
        ],
    )
    fallback = FakeProvider(name="gemini", responses=[LLMResult(content="from gemini")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from gemini"
    assert primary.calls.count("generate") == 2
    mock_llm_retry_sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_groq_success_means_no_fallback_ever_tried(mock_llm_retry_sleep):
    primary = FakeProvider(name="groq", responses=[LLMResult(content="hello")])
    fallback = FakeProvider(name="gemini", responses=[LLMResult(content="should never be used")])
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "hello"
    assert meta.fallback_used is False
    assert fallback.calls == []
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_full_groq_gemini_openrouter_fallback_chain(mock_llm_retry_sleep):
    groq = FakeProvider(name="groq", responses=[ProviderError("429 quota exceeded", retryable=False)])
    gemini = FakeProvider(name="gemini", responses=[ProviderError("429 RESOURCE_EXHAUSTED", retryable=False)])
    openrouter = FakeProvider(name="openrouter", responses=[LLMResult(content="from openrouter")])
    svc = make_service(groq, [gemini, openrouter])

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert result.content == "from openrouter"
    assert meta.provider == "openrouter"
    assert meta.fallback_used is True
    assert meta.fallback_from == "groq"
    assert groq.calls.count("generate") == 1
    assert gemini.calls.count("generate") == 1
    assert openrouter.calls.count("generate") == 1
    mock_llm_retry_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_full_chain_raises_when_every_provider_fails(mock_llm_retry_sleep):
    groq = FakeProvider(name="groq", responses=[ProviderError("429", retryable=False)])
    gemini = FakeProvider(name="gemini", responses=[ProviderError("429", retryable=False)])
    openrouter = FakeProvider(name="openrouter", responses=[ProviderError("429", retryable=False)])
    svc = make_service(groq, [gemini, openrouter])

    with pytest.raises(LLMUnavailableError):
        await svc.generate([LLMMessage(role=Role.USER, content="hi")])


@pytest.mark.asyncio
async def test_observability_meta_records_actual_provider_and_model_on_fallback():
    primary = FakeProvider(name="groq", model="openai/gpt-oss-120b", responses=[ProviderError("429", retryable=False)])
    fallback = FakeProvider(
        name="gemini",
        model="gemini-3.6-flash",
        responses=[LLMResult(content="ok", usage=Usage(input_tokens=5, output_tokens=3, total_tokens=8))],
    )
    svc = make_service(primary, fallback)

    result, meta = await svc.generate([LLMMessage(role=Role.USER, content="hi")])

    assert meta.provider == "gemini"
    assert meta.model == "gemini-3.6-flash"
    assert meta.fallback_used is True
    assert meta.fallback_from == "groq"
    assert meta.usage.total_tokens == 8
    assert meta.latency_ms >= 0
    assert meta.request_id.startswith("req_")
