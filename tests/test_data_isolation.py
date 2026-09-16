"""Data isolation tests: a user must never see or act on another user's
conversations, issues, or appointments. Ownership chain is always
Appointment <- Issue <- Conversation <- User — never patient_name/email.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models.issue import Issue, IssueStatus
from app.services import appointment_service, memory
from app.services.agent import Agent, get_agent
from app.services.llm.base import LLMResult
from app.services.llm.service import LLMService
from tests.fakes import FakeProvider

TEST_SETTINGS = Settings(jwt_secret_key="test-secret-key-for-isolation-tests", jwt_algorithm="HS256")


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _register(client, email: str) -> tuple[str, str]:
    resp = client.post("/auth/register", json={"email": email, "password": "supersecret123"})
    body = resp.json()
    return body["user"]["id"], body["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _book_appointment_for(db_session, user_id: str) -> tuple[str, str]:
    """Directly creates a booked appointment owned by `user_id`. Returns
    (conversation_id, issue_id)."""
    conv = memory.get_or_create_conversation(db_session, user_id, None)
    issue = Issue(
        conversation_id=conv.id,
        symptoms=["headache"],
        department="Neurology",
        status=IssueStatus.READY_FOR_BOOKING,
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="Patient A",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache.",
    )
    return conv.id, issue.id


def _fake_agent() -> Agent:
    provider = FakeProvider(
        name="gemini",
        responses=[LLMResult(content=json.dumps({"issues": [], "reply": "hi", "patient_name": None}))],
    )
    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_providers="")
    svc.primary_name = "gemini"
    svc.fallback_names = []
    svc._providers = {"gemini": provider}
    return Agent(svc)


def test_user_a_sees_their_own_appointment(client, db_session):
    user_a_id, token_a = _register(client, "a1@example.com")
    _book_appointment_for(db_session, user_a_id)

    resp = client.get("/appointments", headers=_auth_headers(token_a))

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_user_b_cannot_see_user_a_appointments(client, db_session):
    user_a_id, _ = _register(client, "a2@example.com")
    _, token_b = _register(client, "b2@example.com")
    _book_appointment_for(db_session, user_a_id)

    resp = client.get("/appointments", headers=_auth_headers(token_b))

    assert resp.status_code == 200
    assert resp.json() == []


def test_user_b_cannot_access_user_a_conversation_via_chat(client, db_session):
    user_a_id, _ = _register(client, "a3@example.com")
    _, token_b = _register(client, "b3@example.com")
    conv_id, _ = _book_appointment_for(db_session, user_a_id)

    app.dependency_overrides[get_agent] = _fake_agent

    resp = client.post(
        "/chat",
        json={"conversation_id": conv_id, "message": "hello"},
        headers=_auth_headers(token_b),
    )

    assert resp.status_code == 404


def test_user_a_can_still_continue_their_own_conversation(client, db_session):
    user_a_id, token_a = _register(client, "a4@example.com")
    conv_id, _ = _book_appointment_for(db_session, user_a_id)

    app.dependency_overrides[get_agent] = _fake_agent

    resp = client.post(
        "/chat",
        json={"conversation_id": conv_id, "message": "hello again"},
        headers=_auth_headers(token_a),
    )

    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == conv_id


def test_user_b_cannot_create_appointment_against_user_a_issue(client, db_session):
    user_a_id, _ = _register(client, "a5@example.com")
    _, token_b = _register(client, "b5@example.com")
    _, issue_id = _book_appointment_for(db_session, user_a_id)

    # a second, still-open issue for user A to try to hijack
    conv = memory.get_or_create_conversation(db_session, user_a_id, None)
    open_issue = Issue(conversation_id=conv.id, symptoms=["dizziness"], status=IssueStatus.READY_FOR_BOOKING)
    db_session.add(open_issue)
    db_session.commit()
    db_session.refresh(open_issue)

    resp = client.post(
        "/appointments",
        json={
            "issue_id": open_issue.id,
            "patient_name": "Intruder",
            "department": "Neurology",
            "visit_date": "2026-10-05",
            "summary": "hijack attempt",
        },
        headers=_auth_headers(token_b),
    )

    assert resp.status_code == 404
    # and no appointment was actually created for it
    refreshed = db_session.get(Issue, open_issue.id)
    assert refreshed.appointment_id is None


def test_user_b_cannot_create_appointment_against_nonexistent_issue(client):
    _, token_b = _register(client, "b6@example.com")

    resp = client.post(
        "/appointments",
        json={
            "issue_id": "iss_doesnotexist",
            "patient_name": "Nobody",
            "department": "ENT",
            "visit_date": "2026-10-05",
            "summary": "n/a",
        },
        headers=_auth_headers(token_b),
    )

    assert resp.status_code == 404
