"""Application configuration loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM provider selection ---
    llm_provider: Literal["gemini", "openrouter", "openai", "groq"] = "groq"
    # Comma-separated, tried in order after the primary — e.g. "gemini,openrouter".
    # Leave empty to disable fallback entirely.
    llm_fallback_providers: str = "gemini,openrouter"

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    openrouter_api_key: str = ""
    # Comma-separated list of model slugs, tried in order — lets the free
    # OpenRouter fallback survive one model being individually
    # rate-limited/overloaded without waiting on Gemini.
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- LLM sampling defaults ---
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1000
    llm_frequency_penalty: float = 0.0

    # --- LLM latency / retry behavior ---
    # Bounds how long any single provider attempt may hang before it's
    # treated as a (retryable) failure and control moves on.
    llm_timeout_seconds: float = 6.0
    # Small pause before retrying the SAME provider/model on a transient
    # failure — kept tiny on purpose so a flaky free-tier model doesn't make
    # the whole chat turn feel slow.
    llm_retry_backoff_seconds: float = 0.5

    @property
    def openrouter_models(self) -> list[str]:
        return [m.strip() for m in self.openrouter_model.split(",") if m.strip()]

    @property
    def fallback_provider_names(self) -> list[str]:
        return [p.strip() for p in self.llm_fallback_providers.split(",") if p.strip()]

    # --- Auth (JWT) ---
    # No default on purpose — a blank/weak secret would silently make every
    # token forgeable. security.py raises loudly if this is empty.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # --- Email (SMTP) — appointment confirmations only, best-effort ---
    # Works with a Gmail "App Password" (Google Account -> Security -> 2-Step
    # Verification -> App passwords) or any other SMTP provider/account.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    # Defaults to smtp_username if left blank.
    smtp_from_email: str = ""

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
