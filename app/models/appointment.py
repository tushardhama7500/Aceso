from __future__ import annotations

import datetime as dt
from functools import partial
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.ids import new_id


class Appointment(Base):
    __tablename__ = "appointments"

    appointment_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=partial(new_id, "apt")
    )
    patient_name: Mapped[str] = mapped_column(String(255))
    department: Mapped[str] = mapped_column(String(100))
    visit_date: Mapped[dt.date] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text)

    # Structured, doctor-facing summary (see app/services/summary_service.py).
    # Kept separate from `summary` (a plain string) to preserve the exact
    # POST /appointments API contract required by the assignment while still
    # giving doctors a richer, validated structured record.
    doctor_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issues: Mapped[list["Issue"]] = relationship(back_populates="appointment")  # noqa: F821
