"""End-to-end Agent test: the scenario from the assignment's "CRITICAL
END-TO-END TEST" section — headaches (-> Neurology) and ringing in ears
(-> ENT) discussed in ONE conversation must produce two separate issues and
two separate appointments, each with its own summary. The LLM is fully
mocked (FakeProvider) — no network calls, no API quota used.
"""
from __future__ import annotations

import json

import pytest

from app.models.appointment import Appointment
from app.models.issue import Issue, IssueStatus
from app.services.agent import Agent
from app.services.llm.base import LLMResult, ToolCall
from app.services.llm.service import LLMService
from tests.fakes import FakeProvider


@pytest.mark.asyncio
async def test_two_unrelated_issues_produce_two_appointments(db_session, monkeypatch):
    provider = FakeProvider(name="gemini")
    svc = LLMService.__new__(LLMService)
    from app.config import Settings

    svc.settings = Settings(llm_provider="gemini", llm_fallback_provider=None)
    svc.primary_name = "gemini"
    svc.fallback_name = None
    svc._providers = {"gemini": provider}
    agent = Agent(svc)

    # --- Turn 1: headache, incomplete -> follow-up question, still collecting
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
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
            )
        )
    ]
    conv_id, reply = await agent.process(db_session, None, "I've been having recurring headaches.")
    assert "how long" in reply.lower()

    issues = db_session.query(Issue).filter(Issue.conversation_id == conv_id).all()
    assert len(issues) == 1
    headache_issue_id = issues[0].id
    assert issues[0].status == IssueStatus.COLLECTING

    # --- Turn 2: give duration + name + date -> ready_for_booking, triggers tool call
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": headache_issue_id,
                            "symptoms": ["headache"],
                            "duration": "two weeks",
                            "severity": None,
                            "relevant_context": ["Occurs several times a week"],
                            "department": "Neurology",
                            "status": "ready_for_booking",
                            "preferred_date": "2026-09-20",
                        }
                    ],
                    "reply": "Based on the symptoms you've described, Neurology would be an appropriate "
                    "department to consult.",
                    "patient_name": "John Doe",
                }
            )
        ),
        # tool-call response for booking
        LLMResult(tool_calls=[ToolCall(id="call_1", name="create_appointment", arguments={
            "patient_name": "John Doe", "department": "Neurology", "visit_date": "2026-09-20",
            "summary": "Patient reports headache for two weeks.",
        })]),
        # final phrasing after tool result
        LLMResult(content="Your Neurology appointment has been booked for 2026-09-20."),
    ]
    conv_id, reply = await agent.process(
        db_session, conv_id, "Around two weeks, several times a week. I'm John Doe, September 20 works."
    )
    assert "neurology" in reply.lower()

    appointments = db_session.query(Appointment).all()
    assert len(appointments) == 1
    assert appointments[0].department == "Neurology"

    issue = db_session.get(Issue, headache_issue_id)
    assert issue.status == IssueStatus.BOOKED
    assert issue.appointment_id == appointments[0].appointment_id
    assert issue.doctor_summary["chief_concern"] == "headache"

    # --- Turn 3: a SECOND, unrelated issue in the same conversation
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["ringing in ears"],
                            "duration": "3 days",
                            "severity": None,
                            "relevant_context": [],
                            "department": "ENT",
                            "status": "ready_for_booking",
                            "preferred_date": "2026-09-22",
                        }
                    ],
                    "reply": "Based on the symptoms you've described, ENT would be an appropriate department "
                    "to consult.",
                    "patient_name": None,
                }
            )
        ),
        LLMResult(tool_calls=[ToolCall(id="call_2", name="create_appointment", arguments={
            "patient_name": "John Doe", "department": "ENT", "visit_date": "2026-09-22",
            "summary": "Patient reports ringing in ears for 3 days.",
        })]),
        LLMResult(content="Your ENT appointment has been booked for 2026-09-22."),
    ]
    conv_id, reply = await agent.process(
        db_session, conv_id, "I also have ringing in my ears for the past 3 days. September 22 please."
    )
    assert "ent" in reply.lower()

    # Final assertions: two separate issues, two separate appointments
    all_issues = db_session.query(Issue).filter(Issue.conversation_id == conv_id).all()
    assert len(all_issues) == 2
    assert {i.department for i in all_issues} == {"Neurology", "ENT"}
    assert all(i.status == IssueStatus.BOOKED for i in all_issues)

    all_appointments = db_session.query(Appointment).all()
    assert len(all_appointments) == 2
    assert {a.department for a in all_appointments} == {"Neurology", "ENT"}
    # each appointment has its own doctor summary
    summaries = {a.department: a.doctor_summary for a in all_appointments}
    assert summaries["Neurology"]["chief_concern"] == "headache"
    assert summaries["ENT"]["chief_concern"] == "ringing in ears"


@pytest.mark.asyncio
async def test_conversation_memory_persists_across_calls(db_session):
    provider = FakeProvider(name="gemini")
    from app.config import Settings

    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_provider=None)
    svc.primary_name = "gemini"
    svc.fallback_name = None
    svc._providers = {"gemini": provider}
    agent = Agent(svc)

    provider.responses = [
        LLMResult(content=json.dumps({"issues": [], "reply": "Hi, how can I help?", "patient_name": None}))
    ]
    conv_id, _ = await agent.process(db_session, None, "hello")

    from app.services import memory

    history = memory.get_history(db_session, conv_id)
    assert len(history) == 2  # user + assistant
    assert history[0].content == "hello"
