from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    conversation_id: str
    message: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: dt.datetime


class MessageItem(BaseModel):
    role: str
    content: str
    created_at: dt.datetime
