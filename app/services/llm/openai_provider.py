from __future__ import annotations

from app.services.llm.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """Optional provider. Not required for the app to function — see README
    (free-first requirement: Gemini + OpenRouter cover the default path)."""

    def __init__(self, *, api_key: str, model: str):
        super().__init__(name="openai", api_key=api_key, model=model, base_url=None)
