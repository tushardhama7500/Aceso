from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.agent import Agent, get_agent

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: Session = Depends(get_db), agent: Agent = Depends(get_agent)):
    conversation_id, reply = await agent.process(db, body.conversation_id, body.message)
    return ChatResponse(conversation_id=conversation_id, message=reply)
