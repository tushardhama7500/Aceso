from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.appointment import (
    AppointmentCreateRequest,
    AppointmentCreateResponse,
    AppointmentListItem,
)
from app.services import appointment_service

router = APIRouter(tags=["appointments"])


@router.post("/appointments", response_model=AppointmentCreateResponse, status_code=201)
def create_appointment(body: AppointmentCreateRequest, db: Session = Depends(get_db)):
    appointment = appointment_service.create_appointment(
        db,
        patient_name=body.patient_name,
        department=body.department,
        visit_date=body.visit_date,
        summary=body.summary,
    )
    return AppointmentCreateResponse(appointment_id=appointment.appointment_id)


@router.get("/appointments", response_model=list[AppointmentListItem])
def list_appointments(db: Session = Depends(get_db)):
    return appointment_service.list_appointments(db)
