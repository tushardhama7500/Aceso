"""Conversation memory: everything the Agent needs to reconstruct context
lives in PostgreSQL — no in-process state. See README "Memory".
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.issue import Issue, IssueStatus
from app.models.message import Message, MessageRole
from app.schemas.analysis import ExtractedIssue

OPEN_STATUSES = (IssueStatus.COLLECTING, IssueStatus.READY_FOR_BOOKING)


class ConversationAccessError(Exception):
    """Raised when `conversation_id` doesn't exist or belongs to a different
    user. Deliberately the same error either way — confirming that a
    conversation ID exists but belongs to someone else is itself a leak."""


def get_or_create_conversation(db: Session, user_id: str, conversation_id: Optional[str]) -> Conversation:
    if conversation_id:
        existing = db.get(Conversation, conversation_id)
        if existing is not None:
            if existing.user_id != user_id:
                raise ConversationAccessError(conversation_id)
            return existing
    conversation = Conversation(user_id=user_id)
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


def list_conversations_for_user(db: Session, user_id: str) -> list[Conversation]:
    """Ordered most-recently-active first, like a chat history sidebar.
    `Conversation.updated_at` only changes when the conversation row itself
    is written (e.g. patient_name first set) — not on every new message — so
    this orders by the latest message's timestamp instead, falling back to
    the conversation's own created_at for the (practically nonexistent)
    case of a conversation with no messages yet."""
    latest_message = (
        select(Message.conversation_id, func.max(Message.created_at).label("last_activity"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    stmt = (
        select(Conversation)
        .outerjoin(latest_message, latest_message.c.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
        .order_by(func.coalesce(latest_message.c.last_activity, Conversation.created_at).desc())
    )
    return list(db.scalars(stmt))


def get_first_user_message(db: Session, conversation_id: str) -> Optional[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == MessageRole.USER)
        .order_by(Message.created_at.asc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_open_issues(db: Session, conversation_id: str) -> list[Issue]:
    stmt = (
        select(Issue)
        .where(Issue.conversation_id == conversation_id, Issue.status.in_(OPEN_STATUSES))
        .order_by(Issue.created_at.asc())
    )
    return list(db.scalars(stmt))


def get_all_issues(db: Session, conversation_id: str) -> list[Issue]:
    """Every issue ever raised in this conversation, regardless of status —
    used to build the LLM's "known issues" context so an already-BOOKED
    issue stays referenceable by issue_ref (e.g. for a reschedule request).
    `get_open_issues` alone would make a booked issue invisible to the
    model, which then has no id to reuse and creates a duplicate instead."""
    stmt = select(Issue).where(Issue.conversation_id == conversation_id).order_by(Issue.created_at.asc())
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
