from __future__ import annotations

import datetime as dt
from functools import partial
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.ids import new_id


class LLMRequestLog(Base):
    """One row per LLM call. Backs GET /metrics aggregates.

    Structured logging (see app/services/observability.py) is the primary
    observability mechanism; this table exists so /metrics survives restarts
    and can compute aggregates without re-parsing logs.
    """

    __tablename__ = "llm_request_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=partial(new_id, "req"))
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))

    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    ttft_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)

    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_from: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
