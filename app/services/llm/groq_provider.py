from __future__ import annotations

from app.services.llm.openai_compatible import OpenAICompatibleProvider

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(OpenAICompatibleProvider):
    """Groq exposes an OpenAI-compatible API, so this simply points the
    shared OpenAI-compatible client at Groq's base URL — used as Aceso's
    primary provider for its very low inference latency."""

    def __init__(self, *, api_key: str, model: str):
        super().__init__(name="groq", api_key=api_key, model=model, base_url=GROQ_BASE_URL)
