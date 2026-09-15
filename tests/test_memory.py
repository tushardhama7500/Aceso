"""Memory-layer tests: conversation/message persistence and issue upsert
merging, independent of the Agent/LLM."""
from __future__ import annotations

from app.models.issue import IssueStatus
from app.models.message import MessageRole
from app.schemas.analysis import ExtractedIssue
from app.services import memory


def test_get_or_create_conversation_creates_when_missing(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    assert conv.id.startswith("conv_")


def test_get_or_create_conversation_reuses_existing(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    same = memory.get_or_create_conversation(db_session, conv.id)
    assert same.id == conv.id


def test_messages_persist_and_are_retrievable_in_order(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    memory.add_message(db_session, conv.id, MessageRole.USER, "hello")
    memory.add_message(db_session, conv.id, MessageRole.ASSISTANT, "hi there")

    history = memory.get_history(db_session, conv.id)
    assert [m.content for m in history] == ["hello", "hi there"]
    assert history[0].role == MessageRole.USER
    assert history[1].role == MessageRole.ASSISTANT


def test_sync_issue_creates_new_issue_when_no_ref(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    extracted = ExtractedIssue(symptoms=["headache"], status="collecting")

    issue = memory.sync_issue(db_session, conv.id, extracted)

    assert issue.symptoms == ["headache"]
    assert issue.status == IssueStatus.COLLECTING
    assert len(memory.get_open_issues(db_session, conv.id)) == 1


def test_sync_issue_updates_existing_issue_by_ref_and_merges_fields(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    first = memory.sync_issue(db_session, conv.id, ExtractedIssue(symptoms=["headache"], status="collecting"))

    updated = memory.sync_issue(
        db_session,
        conv.id,
        ExtractedIssue(
            issue_ref=first.id,
            symptoms=["headache"],
            duration="two weeks",
            department="Neurology",
            status="ready_for_booking",
            preferred_date="2026-09-20",
        ),
    )

    assert updated.id == first.id
    assert updated.symptoms == ["headache"]  # no duplicate entries
    assert updated.duration == "two weeks"
    assert updated.department == "Neurology"
    assert updated.status == IssueStatus.READY_FOR_BOOKING
    assert len(memory.get_open_issues(db_session, conv.id)) == 1


def test_two_unrelated_issue_extractions_create_two_issues(db_session):
    conv = memory.get_or_create_conversation(db_session, None)
    memory.sync_issue(db_session, conv.id, ExtractedIssue(symptoms=["headache"], status="collecting"))
    memory.sync_issue(db_session, conv.id, ExtractedIssue(symptoms=["ringing in ears"], status="collecting"))

    open_issues = memory.get_open_issues(db_session, conv.id)
    assert len(open_issues) == 2
    assert {tuple(i.symptoms) for i in open_issues} == {("headache",), ("ringing in ears",)}
