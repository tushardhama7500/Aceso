from __future__ import annotations

import os
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app import models  # noqa: F401  ensure models register on Base.metadata
from app.services import user_service


@pytest.fixture(autouse=True)
def mock_llm_retry_sleep(monkeypatch):
    """LLMService's small retry-backoff sleep is real (asyncio.sleep) in
    production, but must never actually slow the test suite down. Autouse so
    every test gets it for free; a test that wants to assert on backoff
    behavior can still request this fixture by name to inspect the mock."""
    mock_sleep = AsyncMock()
    monkeypatch.setattr("app.services.llm.service._backoff_sleep", mock_sleep)
    return mock_sleep


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite DB per test, sharing one connection so
    schema/data are visible across the session (SQLite :memory: is
    per-connection otherwise)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def test_user(db_session):
    """A persisted User row — Conversation.user_id is a required FK, so any
    test that creates/loads a conversation needs a real owning user."""
    return user_service.create_user(db_session, email="patient@example.com", password="testpassword123")

