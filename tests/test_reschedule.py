"""Regression test for a bug found in manual testing: asking to change the
date of an already-booked appointment created a SECOND appointment instead
of updating the first one.

Root cause: `get_open_issues` (COLLECTING/READY_FOR_BOOKING only) fed the
"known issues" LLM context, so a BOOKED issue vanished from that context
the turn after it was booked — the model had no `issue_ref` to reuse for
"change my appointment date" and extracted a brand-new issue instead, which
the normal booking flow then dutifully booked as a second appointment.

Fixed by (a) including booked issues in the known-issues context so the
model can reference them, and (b) treating any turn where `issue_ref`
resolves to an already-booked issue as a reschedule — updating the existing
Appointment row directly rather than ever re-entering the booking flow.
"""
from __future__ import annotations

import datetime as dt
import json
from unittest.mock import MagicMock

import pytest

from app.models.appointment import Appointment
from app.models.issue import Issue, IssueStatus
from app.services.agent import Agent
from app.services.llm.base import LLMResult, ToolCall
from app.services.llm.service import LLMService
from tests.fakes import FakeProvider


def _make_agent(provider: FakeProvider) -> Agent:
    from app.config import Settings

    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_providers="")
    svc.primary_name = "gemini"
    svc.fallback_names = []
    svc._providers = {"gemini": provider}
    return Agent(svc)


@pytest.mark.asyncio
async def test_changing_the_date_of_a_booked_appointment_updates_it_in_place(db_session, test_user):
    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)

    # --- Turn 1: book a General Medicine appointment for 2026-09-20
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["anxiety"],
                            "duration": "two weeks",
                            "severity": "6/10",
                            "relevant_context": [],
                            "department": "General Medicine",
                            "status": "ready_for_booking",
                            "preferred_date": "2026-09-20",
                        }
                    ],
                    "reply": "Thank you. I'll get that booked.",
                    "patient_name": "Tushar Dhama",
                }
            )
        ),
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="create_appointment",
                    arguments={
                        "patient_name": "Tushar Dhama",
                        "department": "General Medicine",
                        "visit_date": "2026-09-20",
                        "summary": "Patient reports feeling anxious for two weeks.",
                    },
                )
            ]
        ),
        LLMResult(content="Your General Medicine appointment has been booked for 2026-09-20."),
    ]
    conv_id, reply = await agent.process(
        db_session, test_user.id, None, "I've been feeling anxious for two weeks, moderate, I'm Tushar Dhama, Sep 20 2026."
    )
    assert "2026-09-20" in reply

    issues = db_session.query(Issue).filter(Issue.conversation_id == conv_id).all()
    assert len(issues) == 1
    issue_id = issues[0].id
    assert issues[0].status == IssueStatus.BOOKED
    original_appointment_id = issues[0].appointment_id
    assert original_appointment_id is not None

    # --- Turn 2: ask to change the date. A realistic (if imperfect) model
    # correctly sets issue_ref to the booked issue, but — not fully
    # understanding it's already booked — sends status=ready_for_booking
    # again. The Agent must not treat this as a new booking.
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
                            "preferred_date": "2026-09-19",
                        }
                    ],
                    "reply": "Sure, I've updated your appointment date.",
                    "patient_name": None,
                }
            )
        )
    ]
    conv_id, reply2 = await agent.process(db_session, test_user.id, conv_id, "change my general medicine appointment to 19 sep")

    assert "rescheduled" in reply2.lower()
    assert "2026-09-19" in reply2

    # Exactly one appointment must exist — updated in place, not duplicated.
    all_appointments = db_session.query(Appointment).all()
    assert len(all_appointments) == 1
    assert all_appointments[0].appointment_id == original_appointment_id
    assert all_appointments[0].visit_date == dt.date(2026, 9, 19)

    # The issue must remain BOOKED (not flipped back to ready_for_booking)
    # and still point at the same appointment.
    refreshed_issue = db_session.get(Issue, issue_id)
    assert refreshed_issue.status == IssueStatus.BOOKED
    assert refreshed_issue.appointment_id == original_appointment_id

    # No tool call should have been made for the reschedule turn — it never
    # re-enters the booking flow.
    assert provider.calls == ["generate", "generate_with_tools", "generate", "generate"]


@pytest.mark.asyncio
async def test_reschedule_sends_a_confirmation_email_to_the_account_holder(db_session, test_user, monkeypatch):
    """Regression test: the reschedule path was wired to update the
    Appointment row but never actually called EmailService — a real
    reschedule silently sent no email at all."""
    fake_email_service = MagicMock()
    fake_email_service.send_appointment_reschedule_notice.return_value = True
    monkeypatch.setattr(
        "app.services.appointment_service.get_email_service", lambda: fake_email_service
    )

    provider = FakeProvider(name="gemini")
    agent = _make_agent(provider)

    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["headache"],
                            "duration": "two weeks",
                            "severity": None,
                            "relevant_context": [],
                            "department": "Neurology",
                            "status": "ready_for_booking",
                            "preferred_date": "2026-09-20",
                        }
                    ],
                    "reply": "Thank you. I'll get that booked.",
                    "patient_name": "Tushar Dhama",
                }
            )
        ),
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="create_appointment",
                    arguments={
                        "patient_name": "Tushar Dhama",
                        "department": "Neurology",
                        "visit_date": "2026-09-20",
                        "summary": "Patient reports headache for two weeks.",
                    },
                )
            ]
        ),
        LLMResult(content="Your Neurology appointment has been booked for 2026-09-20."),
    ]
    conv_id, _ = await agent.process(
        db_session, test_user.id, None, "Headache for two weeks, I'm Tushar Dhama, Sep 20 2026."
    )
    fake_email_service.send_appointment_confirmation.assert_called_once()
    fake_email_service.send_appointment_reschedule_notice.assert_not_called()

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
                            "department": "Neurology",
                            "status": "ready_for_booking",
                            "preferred_date": "2026-09-18",
                        }
                    ],
                    "reply": "Sure, I've updated your appointment date.",
                    "patient_name": None,
                }
            )
        )
    ]
    await agent.process(db_session, test_user.id, conv_id, "change my neurology appointment to 18 sep 2026")

    fake_email_service.send_appointment_reschedule_notice.assert_called_once_with(
        to_email=test_user.email,
        patient_name="Tushar Dhama",
        department="Neurology",
        visit_date=dt.date(2026, 9, 18),
        appointment_id=fake_email_service.send_appointment_reschedule_notice.call_args.kwargs["appointment_id"],
    )
    # still exactly one confirmation email overall — the reschedule sends
    # its own distinct notice, not a second "confirmed" email.
    fake_email_service.send_appointment_confirmation.assert_called_once()


def _make_booked_issue(db_session, test_user, **overrides):
    from app.services import appointment_service, memory

    conv = memory.get_or_create_conversation(db_session, test_user.id, None)
    appointment = appointment_service.create_appointment(
        db_session,
        patient_name="Tushar Dhama",
        department="General Medicine",
        visit_date=dt.date(2026, 9, 20),
        summary="Patient reports anxiety.",
    )
    defaults = dict(
        conversation_id=conv.id,
        symptoms=["anxiety"],
        department="General Medicine",
        preferred_date="2026-09-20",
        status=IssueStatus.BOOKED,
        appointment_id=appointment.appointment_id,
    )
    defaults.update(overrides)
    issue = Issue(**defaults)
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    return conv, issue


def test_reschedule_context_marks_booked_issue_with_its_appointment_id(db_session, test_user):
    from app.services.agent import _describe_known_issue

    _, issue = _make_booked_issue(db_session, test_user)

    described = _describe_known_issue(issue)

    assert "status=booked" in described
    assert f"appointment_id={issue.appointment_id!r}" in described


def test_booked_issues_are_included_in_known_issues_context(db_session, test_user):
    from app.services import memory

    conv, issue = _make_booked_issue(db_session, test_user)

    # get_open_issues (COLLECTING/READY_FOR_BOOKING only) must NOT include
    # it — that's the original, still-correct behavior for other purposes —
    # but get_all_issues (what the Agent now uses for LLM context) must.
    assert memory.get_open_issues(db_session, conv.id) == []
    all_issues = memory.get_all_issues(db_session, conv.id)
    assert len(all_issues) == 1
    assert all_issues[0].id == issue.id
