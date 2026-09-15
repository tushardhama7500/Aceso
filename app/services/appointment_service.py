"""Appointment persistence. The only code path allowed to write appointments —
the LLM never touches the database directly; it only ever calls the
`create_appointment` tool, which is executed here."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment import Appointment


def create_appointment(
    db: Session,
    *,
    patient_name: str,
    department: str,
    visit_date: dt.date,
    summary: str,
    doctor_summary: Optional[dict] = None,
) -> Appointment:
    appointment = Appointment(
        patient_name=patient_name,
        department=department,
        visit_date=visit_date,
        summary=summary,
        doctor_summary=doctor_summary,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def list_appointments(db: Session) -> list[Appointment]:
    return list(db.scalars(select(Appointment).order_by(Appointment.created_at.desc())))


def get_appointment(db: Session, appointment_id: str) -> Optional[Appointment]:
    return db.get(Appointment, appointment_id)
