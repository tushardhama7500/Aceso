"""Persists per-LLM-call metrics and computes the /metrics aggregate.

Structured logging (see app/services/llm/service.py, which logs a JSON line
per call) is the primary observability mechanism; this table exists so
/metrics survives restarts without re-parsing logs.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.llm_request_log import LLMRequestLog
from app.services.llm.service import LLMCallMeta


def record_llm_call(db: Session, conversation_id: Optional[str], meta: LLMCallMeta) -> None:
    log = LLMRequestLog(
        id=meta.request_id,
        conversation_id=conversation_id,
        provider=meta.provider,
        model=meta.model,
        input_tokens=meta.usage.input_tokens,
        output_tokens=meta.usage.output_tokens,
        cached_tokens=meta.usage.cached_tokens,
        total_tokens=meta.usage.total_tokens,
        ttft_ms=meta.ttft_ms,
        latency_ms=meta.latency_ms,
        fallback_used=meta.fallback_used,
        fallback_from=meta.fallback_from,
    )
    db.merge(log)
    db.commit()


def get_metrics_summary(db: Session) -> dict:
    logs = list(db.scalars(select(LLMRequestLog)))
    total = len(logs)
    if total == 0:
        return {
            "total_llm_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "average_latency_ms": None,
            "average_ttft_ms": None,
            "provider_usage": {},
            "fallback_count": 0,
        }

    ttft_values = [log.ttft_ms for log in logs if log.ttft_ms is not None]
    provider_usage: dict[str, int] = {}
    for log in logs:
        provider_usage[log.provider] = provider_usage.get(log.provider, 0) + 1

    return {
        "total_llm_requests": total,
        "total_input_tokens": sum(log.input_tokens or 0 for log in logs),
        "total_output_tokens": sum(log.output_tokens or 0 for log in logs),
        "total_tokens": sum(log.total_tokens or 0 for log in logs),
        "average_latency_ms": round(sum(log.latency_ms for log in logs) / total, 2),
        "average_ttft_ms": round(sum(ttft_values) / len(ttft_values), 2) if ttft_values else None,
        "provider_usage": provider_usage,
        "fallback_count": sum(1 for log in logs if log.fallback_used),
    }
