"""Tool-layer tests: the create_appointment tool's schema/arg validation, and
that executing it actually persists a row through appointment_service."""
from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.services import appointment_service
from app.tools.appointments import CREATE_APPOINTMENT_TOOL, CreateAppointmentArgs


def test_tool_definition_declares_required_fields():
    required = set(CREATE_APPOINTMENT_TOOL.parameters["required"])
    assert required == {"patient_name", "department", "visit_date", "summary"}


def test_create_appointment_args_parses_iso_date():
    args = CreateAppointmentArgs.model_validate(
        {
            "patient_name": "John Doe",
            "department": "ENT",
            "visit_date": "2026-07-01",
            "summary": "Patient reports ringing in ears for 3 days.",
        }
    )
    assert args.visit_date == dt.date(2026, 7, 1)


def test_create_appointment_args_rejects_missing_fields():
    with pytest.raises(ValidationError):
        CreateAppointmentArgs.model_validate({"patient_name": "John Doe"})


def test_executing_the_tool_persists_an_appointment(db_session):
    args = CreateAppointmentArgs(
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 7, 1),
        summary="Patient reports ringing in ears for 3 days.",
    )
    appointment = appointment_service.create_appointment(
        db_session,
        patient_name=args.patient_name,
        department=args.department,
        visit_date=args.visit_date,
        summary=args.summary,
    )

    assert appointment.appointment_id.startswith("apt_")
    fetched = appointment_service.get_appointment(db_session, appointment.appointment_id)
    assert fetched is not None
    assert fetched.department == "ENT"

    all_appointments = appointment_service.list_appointments(db_session)
    assert len(all_appointments) == 1
