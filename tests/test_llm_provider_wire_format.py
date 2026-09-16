"""Regression tests for two provider wire-format bugs hit in manual testing:

1. Gemini's `generate_with_tools` failed with "contents are required" when
   the Agent's booking flow sends a single SYSTEM-role message (Gemini,
   unlike OpenAI-compatible APIs, needs at least one user/model content
   turn; a system-only message list produced an empty `contents` list).
2. OpenRouter (via the shared OpenAI-compatible client) can return HTTP 200
   with an `error` body instead of `choices` for overloaded/rate-limited
   free models. The code used to index `resp.choices[0]` unconditionally,
   raising an unhandled TypeError instead of a catchable ProviderError.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest
import requests
from google.genai import errors as genai_errors

from app.services.llm.base import LLMMessage, ProviderError, Role
from app.services.llm.gemini_provider import _is_retryable as gemini_is_retryable
from app.services.llm.gemini_provider import _split_system_and_contents
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.openai_compatible import _is_retryable as openai_is_retryable


def test_system_only_messages_produce_non_empty_gemini_contents():
    messages = [LLMMessage(role=Role.SYSTEM, content="Call create_appointment now with these values.")]

    system_instruction, contents = _split_system_and_contents(messages)

    assert len(contents) == 1
    assert contents[0].role == "user"
    assert system_instruction is None


def test_mixed_system_and_user_messages_keep_system_as_instruction():
    messages = [
        LLMMessage(role=Role.SYSTEM, content="You are Aceso."),
        LLMMessage(role=Role.USER, content="I have a headache."),
    ]

    system_instruction, contents = _split_system_and_contents(messages)

    assert system_instruction == "You are Aceso."
    assert len(contents) == 1
    assert contents[0].role == "user"


def test_openai_compatible_raises_provider_error_on_choiceless_200_response():
    provider = OpenAICompatibleProvider(name="openrouter", api_key="k", model="m")
    resp = SimpleNamespace(choices=None, error={"message": "Upstream error: Service temporarily overloaded"})

    with pytest.raises(ProviderError, match="Service temporarily overloaded"):
        provider._require_choice(resp)


def test_openai_compatible_returns_choice_when_present():
    provider = OpenAICompatibleProvider(name="openrouter", api_key="k", model="m")
    choice = SimpleNamespace(message=SimpleNamespace(content="hi"), finish_reason="stop")
    resp = SimpleNamespace(choices=[choice], error=None)

    assert provider._require_choice(resp) is choice


# ----------------------------------------------------------------------
# Error classification: quota/auth/config errors are NOT worth retrying
# (they'll fail identically again); timeouts/connection errors/5xx ARE.
# ----------------------------------------------------------------------

_request = httpx.Request("POST", "https://example.com")


def _openai_status_error(cls, status_code: int):
    response = httpx.Response(status_code=status_code, request=_request)
    return cls(f"http {status_code}", response=response, body=None)


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: _openai_status_error(openai.RateLimitError, 429),
        lambda: _openai_status_error(openai.AuthenticationError, 401),
        lambda: _openai_status_error(openai.PermissionDeniedError, 403),
        lambda: _openai_status_error(openai.BadRequestError, 400),
    ],
)
def test_openai_quota_auth_config_errors_are_not_retryable(make_exc):
    assert openai_is_retryable(make_exc()) is False


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: openai.APITimeoutError(request=_request),
        lambda: openai.APIConnectionError(request=_request),
        lambda: _openai_status_error(openai.InternalServerError, 500),
        lambda: _openai_status_error(openai.InternalServerError, 503),
    ],
)
def test_openai_transient_errors_are_retryable(make_exc):
    assert openai_is_retryable(make_exc()) is True


def _gemini_error(cls, code: int):
    resp = requests.Response()
    resp.status_code = code
    resp._content = f'{{"error": {{"code": {code}, "message": "x", "status": "X"}}}}'.encode()
    return cls(code, resp)


@pytest.mark.parametrize("code", [429, 401, 403])
def test_gemini_quota_auth_errors_are_not_retryable(code):
    assert gemini_is_retryable(_gemini_error(genai_errors.ClientError, code)) is False


@pytest.mark.parametrize("code", [500, 503])
def test_gemini_server_errors_are_retryable(code):
    assert gemini_is_retryable(_gemini_error(genai_errors.ServerError, code)) is True


def test_unclassified_exceptions_default_to_retryable():
    # Network-level errors (connection reset, DNS failure, ...) that never
    # made it into a typed SDK exception should still be treated as
    # transient rather than silently skipping the fallback's only retry.
    assert gemini_is_retryable(RuntimeError("boom")) is True
    assert openai_is_retryable(RuntimeError("boom")) is True
