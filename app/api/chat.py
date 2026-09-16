from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import memory
from app.services.agent import Agent, get_agent

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_agent),
    current_user: User = Depends(get_current_user),
):
    try:
        conversation_id, reply = await agent.process(db, current_user.id, body.conversation_id, body.message)
    except memory.ConversationAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found") from e
    return ChatResponse(conversation_id=conversation_id, message=reply)
