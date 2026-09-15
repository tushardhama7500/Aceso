"""Application configuration loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM provider selection ---
    llm_provider: Literal["gemini", "openrouter", "openai"] = "gemini"
    llm_fallback_provider: Optional[Literal["gemini", "openrouter", "openai"]] = "openrouter"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- LLM sampling defaults ---
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1000
    llm_frequency_penalty: float = 0.0

    # --- Database ---
    database_url: str = "postgresql+psycopg2://aceso:aceso@localhost:5432/aceso"

    # --- Misc ---
    cors_origins: str = "*"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
