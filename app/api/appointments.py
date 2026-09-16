from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models.issue import Issue
from app.models.user import User
from app.schemas.appointment import (
    AppointmentCreateRequest,
    AppointmentCreateResponse,
    AppointmentListItem,
)
from app.services import appointment_service

router = APIRouter(tags=["appointments"])


@router.post("/appointments", response_model=AppointmentCreateResponse, status_code=201)
def create_appointment(
    body: AppointmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    issue = db.get(Issue, body.issue_id)
    if issue is None or issue.conversation.user_id != current_user.id:
        # Same response either way — confirming the issue exists but
        # belongs to someone else would itself leak information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    appointment, _created = appointment_service.book_issue_appointment(
        db,
        issue=issue,
        patient_name=body.patient_name,
        department=body.department,
        visit_date=body.visit_date,
        summary=body.summary,
        notify_email=current_user.email,
    )
    return AppointmentCreateResponse(appointment_id=appointment.appointment_id)


@router.get("/appointments", response_model=list[AppointmentListItem])
def list_appointments(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return appointment_service.list_appointments_for_user(db, current_user.id)
