from __future__ import annotations

from app.services.llm.openai_compatible import OpenAICompatibleProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatibleProvider):
    """Free-tier-friendly fallback provider. OpenRouter exposes an
    OpenAI-compatible API, so this simply points the shared OpenAI-compatible
    client at OpenRouter's base URL with a free model."""

    def __init__(self, *, api_key: str, model: str):
        super().__init__(name="openrouter", api_key=api_key, model=model, base_url=OPENROUTER_BASE_URL)
