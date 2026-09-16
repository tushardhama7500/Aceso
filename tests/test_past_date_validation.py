"""Regression tests for a bug found in manual testing: the Agent booked an
appointment for a date that had already passed (today was 2026-09-16, the
booked visit_date was 2026-09-12). Root cause: the LLM was never told what
today's actual date is, so it had no reliable way to resolve a year-less
date ("12 sep") to a date on or after today.

Fixed by (a) injecting today's date into every turn's LLM context with
explicit resolution instructions, and (b) a deterministic backend check in
_maybe_book/_maybe_reschedule that refuses to book/reschedule to a date
before today regardless of what the model outputs — using relative dates
computed from the real clock so this test never rots.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.models.appointment import Appointment
from app.models.issue import Issue, IssueStatus
from app.services.agent import Agent
from app.services.llm.base import LLMResult, Role, ToolCall
from app.services.llm.service import LLMService
from tests.fakes import FakeProvider

YESTERDAY = dt.date.today() - dt.timedelta(days=1)
TOMORROW = dt.date.today() + dt.timedelta(days=1)


def _make_agent(provider: FakeProvider) -> Agent:
    from app.config import Settings

    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_providers="")
    svc.primary_name = "gemini"
    svc.fallback_names = []
    svc._providers = {"gemini": provider}
    return Agent(svc)


@pytest.mark.asyncio
async def test_a_past_preferred_date_is_never_booked(db_session, test_user):
    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)

    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["fever"],
                            "duration": "two days",
                            "severity": None,
                            "relevant_context": [],
                            "department": "General Medicine",
                            "status": "ready_for_booking",
                            "preferred_date": YESTERDAY.isoformat(),
                        }
                    ],
                    "reply": "Thank you.",
                    "patient_name": "Vishal",
                }
            )
        )
    ]

    conv_id, reply = await agent.process(
        db_session, test_user.id, None, f"I have a fever, I'm Vishal, book me for {YESTERDAY.isoformat()}."
    )

    assert "already passed" in reply.lower()
    assert db_session.query(Appointment).count() == 0

    issue = db_session.query(Issue).filter(Issue.conversation_id == conv_id).one()
    assert issue.appointment_id is None
    assert issue.preferred_date is None  # cleared, not left stale
    assert issue.status == IssueStatus.READY_FOR_BOOKING  # unchanged, just missing a valid date


@pytest.mark.asyncio
async def test_after_rejecting_a_past_date_a_future_date_books_normally(db_session, test_user):
    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)

    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["fever"],
                            "duration": "two days",
                            "severity": None,
                            "relevant_context": [],
                            "department": "General Medicine",
                            "status": "ready_for_booking",
                            "preferred_date": YESTERDAY.isoformat(),
                        }
                    ],
                    "reply": "Thank you.",
                    "patient_name": "Vishal",
                }
            )
        )
    ]
    conv_id, reply = await agent.process(db_session, test_user.id, None, "I have a fever, I'm Vishal.")
    assert "already passed" in reply.lower()

    issue_id = db_session.query(Issue).filter(Issue.conversation_id == conv_id).one().id

    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": issue_id,
                            "symptoms": [],
                            "duration": None,
                            "severity": None,
                            "relevant_context": [],
                            "department": "General Medicine",
                            "status": "ready_for_booking",
                            "preferred_date": TOMORROW.isoformat(),
                        }
                    ],
                    "reply": "Great, booking that now.",
                    "patient_name": None,
                }
            )
        ),
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="create_appointment",
                    arguments={
                        "patient_name": "Vishal",
                        "department": "General Medicine",
                        "visit_date": TOMORROW.isoformat(),
                        "summary": "Patient reports fever for two days.",
                    },
                )
            ]
        ),
        LLMResult(content=f"Your General Medicine appointment has been booked for {TOMORROW.isoformat()}."),
    ]
    conv_id, reply = await agent.process(db_session, test_user.id, conv_id, f"Let's do {TOMORROW.isoformat()} instead.")

    assert TOMORROW.isoformat() in reply
    appointment = db_session.query(Appointment).one()
    assert appointment.visit_date == TOMORROW


@pytest.mark.asyncio
async def test_rescheduling_to_a_past_date_is_rejected_and_leaves_appointment_unchanged(db_session, test_user):
    from app.services import appointment_service, memory

    conv = memory.get_or_create_conversation(db_session, test_user.id, None)
    issue = Issue(
        conversation_id=conv.id,
        symptoms=["fever"],
        department="General Medicine",
        preferred_date=TOMORROW.isoformat(),
        status=IssueStatus.READY_FOR_BOOKING,
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="Vishal",
        department="General Medicine",
        visit_date=TOMORROW,
        summary="Patient reports fever.",
    )

    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": issue.id,
                            "symptoms": [],
                            "duration": None,
                            "severity": None,
                            "relevant_context": [],
                            "department": "General Medicine",
                            "status": "ready_for_booking",
                            "preferred_date": YESTERDAY.isoformat(),
                        }
                    ],
                    "reply": "Sure, updating that.",
                    "patient_name": None,
                }
            )
        )
    ]

    conv_id, reply = await agent.process(
        db_session, test_user.id, conv.id, f"Actually change it to {YESTERDAY.isoformat()}."
    )

    assert "already passed" in reply.lower()
    appointment = db_session.query(Appointment).one()
    assert appointment.visit_date == TOMORROW  # unchanged

    refreshed_issue = db_session.get(Issue, issue.id)
    assert refreshed_issue.status == IssueStatus.BOOKED
    assert refreshed_issue.preferred_date == TOMORROW.isoformat()  # reverted, not left stale


def test_todays_date_is_injected_into_every_turns_llm_context():
    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)

    messages = agent._build_messages(conversation=type("C", (), {"patient_name": None})(), history=[], known_issues=[])

    system_texts = [m.content for m in messages if m.role == Role.SYSTEM]
    assert any(dt.date.today().isoformat() in text for text in system_texts)
