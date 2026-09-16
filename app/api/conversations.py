"""Conversation history — list a user's past conversations and replay one's
messages, like a chat client's history sidebar. Read-only: creating/adding
to a conversation still only ever happens through POST /chat.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.chat import ConversationSummary, MessageItem
from app.services import memory

router = APIRouter(tags=["conversations"])

TITLE_MAX_LENGTH = 60


def _title_for(db: Session, conversation: Conversation) -> str:
    first = memory.get_first_user_message(db, conversation.id)
    if first is None or not first.content.strip():
        return "New conversation"
    text = first.content.strip()
    return text if len(text) <= TITLE_MAX_LENGTH else text[:TITLE_MAX_LENGTH].rstrip() + "…"


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    conversations = memory.list_conversations_for_user(db, current_user.id)
    return [
        ConversationSummary(id=c.id, title=_title_for(db, c), updated_at=c.updated_at) for c in conversations
    ]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageItem])
def get_conversation_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        # Same response whether it doesn't exist or belongs to someone else.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    history = memory.get_history(db, conversation_id, limit=500)
    return [MessageItem(role=m.role.value, content=m.content, created_at=m.created_at) for m in history]
