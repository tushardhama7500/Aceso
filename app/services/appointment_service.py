"""Appointment persistence. The only code path allowed to write appointments —
the LLM never touches the database directly; it only ever calls the
`create_appointment` tool, which is executed here.

`book_issue_appointment` and `reschedule_appointment` are also the only code
paths allowed to link/update an Issue's Appointment, and the only places a
confirmation/reschedule email is sent — always strictly after the write has
committed (see EmailService).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.issue import Issue, IssueStatus
from app.services.email_service import EmailService, get_email_service

logger = logging.getLogger("aceso.appointments")


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


def list_appointments_for_user(db: Session, user_id: str) -> list[Appointment]:
    """Appointments are scoped through Issue -> Conversation -> User (no
    direct user_id column on Appointment — the ownership chain is enough
    and needs no schema change on the Appointment table itself)."""
    stmt = (
        select(Appointment)
        .join(Issue, Issue.appointment_id == Appointment.appointment_id)
        .join(Conversation, Conversation.id == Issue.conversation_id)
        .where(Conversation.user_id == user_id)
        .order_by(Appointment.created_at.desc())
    )
    return list(db.scalars(stmt))


def get_appointment(db: Session, appointment_id: str) -> Optional[Appointment]:
    return db.get(Appointment, appointment_id)


def _notify_safely(email_service: EmailService, send) -> None:
    """Runs one of EmailService's send_* methods, never letting a failure —
    or even a raise from a misbehaving EmailService implementation —
    escape into the caller. The write it's notifying about has already
    committed by the time this runs."""
    try:
        send(email_service)
    except Exception as e:  # noqa: BLE001
        logger.warning("email_notification_failed error=%s", e)


def reschedule_appointment(
    db: Session,
    *,
    appointment: Appointment,
    visit_date: Optional[dt.date] = None,
    department: Optional[str] = None,
    notify_email: Optional[str] = None,
    email_service: Optional[EmailService] = None,
) -> bool:
    """Updates an already-booked Appointment in place — the only path that
    changes an existing appointment's date/department. Never creates a new
    row, so a "change my appointment date" request can never turn into a
    second booking for the same issue. Returns whether anything changed."""
    changed = False
    if visit_date is not None and visit_date != appointment.visit_date:
        appointment.visit_date = visit_date
        changed = True
    if department and department != appointment.department:
        appointment.department = department
        changed = True

    if not changed:
        return False

    db.commit()
    db.refresh(appointment)

    if notify_email:
        service = email_service or get_email_service()
        _notify_safely(
            service,
            lambda svc: svc.send_appointment_reschedule_notice(
                to_email=notify_email,
                patient_name=appointment.patient_name,
                department=appointment.department,
                visit_date=appointment.visit_date,
                appointment_id=appointment.appointment_id,
            ),
        )

    return True


def book_issue_appointment(
    db: Session,
    *,
    issue: Issue,
    patient_name: str,
    department: str,
    visit_date: dt.date,
    summary: str,
    doctor_summary: Optional[dict] = None,
    notify_email: Optional[str] = None,
    email_service: Optional[EmailService] = None,
) -> tuple[Appointment, bool]:
    """Atomically book `issue` if it isn't already booked, returning
    `(appointment, created)`. `created=False` means a concurrent
    request/retry already booked this exact issue — the existing
    appointment is returned instead of creating a duplicate.

    Row-level locking (`SELECT ... FOR UPDATE`) closes the race a plain
    read-then-write `if not issue.appointment_id` check would leave open
    between two concurrent tool calls/retries for the same issue.
    """
    locked_issue = db.execute(select(Issue).where(Issue.id == issue.id).with_for_update()).scalar_one()

    if locked_issue.appointment_id:
        existing = db.get(Appointment, locked_issue.appointment_id)
        if existing is not None:
            return existing, False

    appointment = Appointment(
        patient_name=patient_name,
        department=department,
        visit_date=visit_date,
        summary=summary,
        doctor_summary=doctor_summary,
    )
    db.add(appointment)
    db.flush()  # assigns appointment.appointment_id without ending the transaction

    locked_issue.appointment_id = appointment.appointment_id
    locked_issue.status = IssueStatus.BOOKED
    locked_issue.doctor_summary = doctor_summary
    db.commit()
    db.refresh(appointment)

    if notify_email:
        service = email_service or get_email_service()
        _notify_safely(
            service,
            lambda svc: svc.send_appointment_confirmation(
                to_email=notify_email,
                patient_name=patient_name,
                department=department,
                visit_date=visit_date,
                appointment_id=appointment.appointment_id,
            ),
        )

    return appointment, True
