"""Regression tests for a conversation-memory bug found in manual testing:

    User: "I have been having headaches for about two weeks."
    Aceso: asks about severity/other symptoms.
    User: "They happen almost every day. The pain is mostly around my
           forehead and is moderate."
    Aceso: "How long have you been having them?"   <-- WRONG: duration was
           already given as "about two weeks" and must not be re-asked.

These tests pin two things:
1. `Issue` field merging never loses previously-collected fields when a
   later turn's structured update omits them (app/services/memory.py).
2. The context the Agent builds for the LLM on a later turn explicitly
   marks already-known fields as known (not "still missing"), so a
   compliant model has no reason to re-ask (app/services/agent.py).
"""
from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.models.issue import Issue, IssueStatus
from app.services.agent import Agent, _describe_known_issue
from app.services.llm.base import LLMResult
from app.services.llm.service import LLMService
from app.services import memory
from tests.fakes import FakeProvider


def _agent() -> tuple[Agent, FakeProvider]:
    provider = FakeProvider(name="gemini")
    svc = LLMService.__new__(LLMService)
    svc.settings = Settings(llm_provider="gemini", llm_fallback_providers="")
    svc.primary_name = "gemini"
    svc.fallback_names = []
    svc._providers = {"gemini": provider}
    return Agent(svc), provider


@pytest.mark.asyncio
async def test_duration_is_preserved_and_not_re_asked_about(db_session, test_user):
    agent, provider = _agent()

    # Turn 1: patient states symptom + duration. Model asks about severity.
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": None,
                            "symptoms": ["headaches"],
                            "duration": "about two weeks",
                            "severity": None,
                            "relevant_context": [],
                            "department": None,
                            "status": "collecting",
                            "preferred_date": None,
                        }
                    ],
                    "reply": "I'm sorry to hear that. How severe is the pain, and are there any other symptoms?",
                    "patient_name": None,
                }
            )
        )
    ]
    conv_id, reply = await agent.process(
        db_session, test_user.id, None, "I have been having headaches for about two weeks."
    )
    assert "severe" in reply.lower() or "severity" in reply.lower()

    issues = db_session.query(Issue).filter(Issue.conversation_id == conv_id).all()
    assert len(issues) == 1
    issue_id = issues[0].id
    assert issues[0].duration == "about two weeks"

    # Before turn 2, confirm the context the Agent will send to the LLM
    # already marks duration as known (not missing) for this issue.
    open_issues = memory.get_open_issues(db_session, conv_id)
    described = _describe_known_issue(open_issues[0])
    assert "duration='about two weeks'" in described
    assert "still missing:" in described
    assert "duration" not in described.split("still missing:")[1]

    # Turn 2: patient gives frequency/location/severity but does not repeat
    # duration. A correctly-behaving model references the existing issue
    # (issue_ref) and only fills in the genuinely new fields.
    provider.responses = [
        LLMResult(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_ref": issue_id,
                            "symptoms": ["headaches"],
                            "duration": None,
                            "severity": "moderate",
                            "relevant_context": ["almost every day", "forehead"],
                            "department": "Neurology",
                            "status": "collecting",
                            "preferred_date": None,
                        }
                    ],
                    "reply": "Based on the symptoms you've described, Neurology would be an appropriate "
                    "department to consult. What date would you like to come in?",
                    "patient_name": None,
                }
            )
        )
    ]
    conv_id, reply = await agent.process(
        db_session,
        test_user.id,
        conv_id,
        "They happen almost every day. The pain is mostly around my forehead and is moderate.",
    )

    # The bug: the reply must NOT ask again for duration.
    assert "how long" not in reply.lower()
    assert "weeks or months" not in reply.lower()

    updated = db_session.get(Issue, issue_id)
    assert updated.duration == "about two weeks"  # preserved, not overwritten with null
    assert updated.severity == "moderate"  # newly collected field merged in
    assert updated.symptoms == ["headaches"]  # no duplicate entries
    assert set(updated.relevant_context) == {"almost every day", "forehead"}
    assert updated.department == "Neurology"

    # Still a single issue for this conversation — no accidental duplicate
    # issue was created because issue_ref was honored.
    all_issues = db_session.query(Issue).filter(Issue.conversation_id == conv_id).all()
    assert len(all_issues) == 1


def test_known_issue_description_marks_known_fields_as_not_missing(db_session, test_user):
    conv = memory.get_or_create_conversation(db_session, test_user.id, None)
    issue = Issue(
        conversation_id=conv.id,
        symptoms=["headaches"],
        duration="about two weeks",
        status=IssueStatus.COLLECTING,
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)

    described = _describe_known_issue(issue)

    assert "duration='about two weeks'" in described
    missing_section = described.split("still missing:")[1]
    assert "duration" not in missing_section
    assert "severity" in missing_section
    assert "department" in missing_section


def test_known_issue_description_reports_nothing_missing_when_complete(db_session, test_user):
    conv = memory.get_or_create_conversation(db_session, test_user.id, None)
    issue = Issue(
        conversation_id=conv.id,
        symptoms=["headaches"],
        duration="two weeks",
        severity="moderate",
        department="Neurology",
        preferred_date="2026-09-20",
        status=IssueStatus.READY_FOR_BOOKING,
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)

    described = _describe_known_issue(issue)

    assert "nothing — all required info is already collected" in described
