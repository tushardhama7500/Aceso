"""Tests for the LLM abstraction: fallback behavior and structured-output
validation. No real provider SDKs are invoked — everything uses FakeProvider."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.services.llm.base import LLMMessage, LLMResult, ProviderError, Role, ToolCall, Usage
from app.services.llm.service import LLMOutputError, LLMService, LLMUnavailableError
from tests.fakes import FakeProvider


def make_service(primary: FakeProvider, fallback: FakeProvider | None = None) -> LLMService:
    settings = Settings(llm_provider="gemini", llm_fallback_provider="openrouter" if fallback else None)
    svc = LLMService.__new__(LLMService)
    svc.settings = settings
    svc.primary_name = "gemini"
    svc.fallback_name = "openrouter" if fallback else None
    svc._providers = {"gemini": primary}
    if fallback:
        svc._providers["openrouter"] = fallback
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
