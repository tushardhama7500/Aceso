"""API-level tests for POST /chat: follow-up questions, department
recommendation wiring, and tool-call triggering — all via FastAPI's
TestClient with the LLM fully mocked through dependency overrides.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.config import Settings
from app.db import get_db
from app.main import app
from app.services import user_service
from app.services.agent import Agent, get_agent
from app.services.llm.base import LLMResult, ToolCall
from app.services.llm.service import LLMService
from tests.fakes import FakeProvider


def _agent_with_responses(responses: list[LLMResult]) -> Agent:
    provider = FakeProvider(name="gemini", responses=responses)
    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_providers="")
    svc.primary_name = "gemini"
    svc.fallback_names = []
    svc._providers = {"gemini": provider}
    return Agent(svc)


@pytest.fixture()
def client(db_session):
    """These tests exercise chat/booking behavior, not auth itself, so the
    current-user dependency is overridden with a fixed test user rather than
    requiring a real Authorization header on every request — see
    test_auth.py / test_data_isolation.py for the real JWT flow."""

    def override_get_db():
        yield db_session

    test_user = user_service.create_user(db_session, email="patient@example.com", password="testpassword123")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: test_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_incomplete_symptoms_trigger_followup_question(client):
    analysis = {
        "issues": [
            {
                "issue_ref": None,
                "symptoms": ["headache"],
                "duration": None,
                "severity": None,
                "relevant_context": [],
                "department": None,
                "status": "collecting",
                "preferred_date": None,
            }
        ],
        "reply": "How long have you been experiencing the headaches?",
        "patient_name": None,
    }
    app.dependency_overrides[get_agent] = lambda: _agent_with_responses([LLMResult(content=json.dumps(analysis))])

    resp = client.post("/chat", json={"message": "I've been having headaches."})

    assert resp.status_code == 200
    body = resp.json()
    assert "conversation_id" in body
    assert "how long" in body["message"].lower()


@pytest.mark.parametrize(
    "symptom,department",
    [
        ("ringing in my ears", "ENT"),
        ("recurring headaches and numbness", "Neurology"),
        ("knee and joint pain", "Orthopedics"),
    ],
)
def test_department_recommendation_is_surfaced_in_reply(client, symptom, department):
    analysis = {
        "issues": [
            {
                "issue_ref": None,
                "symptoms": [symptom],
                "duration": "a week",
                "severity": None,
                "relevant_context": [],
                "department": department,
                "status": "collecting",
                "preferred_date": None,
            }
        ],
        "reply": f"Based on the symptoms you've described, {department} would be an appropriate department "
        "to consult. What date would you prefer?",
        "patient_name": None,
    }
    app.dependency_overrides[get_agent] = lambda: _agent_with_responses([LLMResult(content=json.dumps(analysis))])

    resp = client.post("/chat", json={"message": f"I have {symptom}, it's been a week."})

    assert resp.status_code == 200
    assert department.lower() in resp.json()["message"].lower()


def test_complete_information_triggers_tool_call_and_booking(client):
    analysis = {
        "issues": [
            {
                "issue_ref": None,
                "symptoms": ["ringing in ears"],
                "duration": "3 days",
                "severity": None,
                "relevant_context": [],
                "department": "ENT",
                "status": "ready_for_booking",
                "preferred_date": "2026-10-01",
            }
        ],
        "reply": "Based on the symptoms you've described, ENT would be an appropriate department to consult.",
        "patient_name": "Jane Smith",
    }
    responses = [
        LLMResult(content=json.dumps(analysis)),
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="create_appointment",
                    arguments={
                        "patient_name": "Jane Smith",
                        "department": "ENT",
                        "visit_date": "2026-10-01",
                        "summary": "Patient reports ringing in ears for 3 days.",
                    },
                )
            ]
        ),
        LLMResult(content="Your ENT appointment has been booked for 2026-10-01."),
    ]
    app.dependency_overrides[get_agent] = lambda: _agent_with_responses(responses)

    resp = client.post(
        "/chat",
        json={"message": "I'm Jane Smith, I've had ringing in my ears for 3 days, October 1st works for me."},
    )

    assert resp.status_code == 200
    assert "ent" in resp.json()["message"].lower()

    appts = client.get("/appointments").json()
    assert len(appts) == 1
    assert appts[0]["department"] == "ENT"
    assert appts[0]["patient_name"] == "Jane Smith"
