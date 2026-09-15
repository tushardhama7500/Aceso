"""Conversation memory: everything the Agent needs to reconstruct context
lives in PostgreSQL — no in-process state. See README "Memory".
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.issue import Issue, IssueStatus
from app.models.message import Message, MessageRole
from app.schemas.analysis import ExtractedIssue

OPEN_STATUSES = (IssueStatus.COLLECTING, IssueStatus.READY_FOR_BOOKING)


def get_or_create_conversation(db: Session, conversation_id: Optional[str]) -> Conversation:
    if conversation_id:
        existing = db.get(Conversation, conversation_id)
        if existing is not None:
            return existing
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(db: Session, conversation_id: str, role: MessageRole, content: str) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_history(db: Session, conversation_id: str, limit: int = 40) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_open_issues(db: Session, conversation_id: str) -> list[Issue]:
    stmt = (
        select(Issue)
        .where(Issue.conversation_id == conversation_id, Issue.status.in_(OPEN_STATUSES))
        .order_by(Issue.created_at.asc())
    )
    return list(db.scalars(stmt))


def _merge_unique(existing: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *new]))


def sync_issue(db: Session, conversation_id: str, extracted: ExtractedIssue) -> Issue:
    """Create or update an Issue row from one LLM-extracted issue update."""
    issue: Optional[Issue] = None
    if extracted.issue_ref:
        candidate = db.get(Issue, extracted.issue_ref)
        if candidate is not None and candidate.conversation_id == conversation_id:
            issue = candidate

    if issue is None:
        issue = Issue(conversation_id=conversation_id)
        db.add(issue)

    if extracted.symptoms:
        issue.symptoms = _merge_unique(issue.symptoms or [], extracted.symptoms)
    if extracted.duration:
        issue.duration = extracted.duration
    if extracted.severity:
        issue.severity = extracted.severity
    if extracted.relevant_context:
        issue.relevant_context = _merge_unique(issue.relevant_context or [], extracted.relevant_context)
    if extracted.department:
        issue.department = extracted.department
    if extracted.preferred_date:
        issue.preferred_date = extracted.preferred_date
    issue.status = IssueStatus(extracted.status)

    db.commit()
    db.refresh(issue)
    return issue
