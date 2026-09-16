from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class AppointmentCreateRequest(BaseModel):
    issue_id: str = Field(..., description="Issue this appointment is for — must belong to the caller")
    patient_name: str = Field(..., min_length=1, max_length=255)
    department: str = Field(..., min_length=1, max_length=100)
    visit_date: dt.date
    summary: str = Field(..., min_length=1)


class AppointmentCreateResponse(BaseModel):
    appointment_id: str


class AppointmentListItem(BaseModel):
    appointment_id: str
    patient_name: str
    department: str
    visit_date: dt.date

    model_config = {"from_attributes": True}
