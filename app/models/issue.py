from __future__ import annotations

import datetime as dt
import enum
from functools import partial
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum as SAEnum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.ids import new_id


class IssueStatus(str, enum.Enum):
    COLLECTING = "collecting"
    READY_FOR_BOOKING = "ready_for_booking"
    BOOKED = "booked"
    CLOSED = "closed"


class Issue(Base):
    """A single medical concern raised within a conversation.

    A conversation may contain multiple independent issues (e.g. headaches
    AND ringing in ears), each tracked and booked separately.
    """

    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=partial(new_id, "iss"))
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )

    symptoms: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    severity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    relevant_context: Mapped[list[str]] = mapped_column(JSON, default=list)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    preferred_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    status: Mapped[IssueStatus] = mapped_column(
        SAEnum(IssueStatus, native_enum=False, length=32), default=IssueStatus.COLLECTING
    )
    appointment_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("appointments.appointment_id"), nullable=True
    )
    doctor_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="issues")  # noqa: F821
    appointment: Mapped[Optional["Appointment"]] = relationship(back_populates="issues")  # noqa: F821
