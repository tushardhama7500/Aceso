"""The `create_appointment` tool: the only tool exposed to the LLM.

The LLM only ever produces (and receives back) JSON arguments/results through
this contract — it never touches the database. `appointment_service` is what
actually executes the write.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from app.services.llm.base import ToolDefinition

CREATE_APPOINTMENT_TOOL = ToolDefinition(
    name="create_appointment",
    description=(
        "Book a medical appointment for the patient. Only call this once the department, a "
        "preferred visit date, and the patient's full name are all known."
    ),
    parameters={
        "type": "object",
        "properties": {
            "patient_name": {"type": "string", "description": "Patient's full name"},
            "department": {"type": "string", "description": "Recommended department, e.g. ENT"},
            "visit_date": {"type": "string", "description": "Preferred visit date, ISO format YYYY-MM-DD"},
            "summary": {
                "type": "string",
                "description": "One or two sentence plain-language summary of the issue for the appointment record",
            },
        },
        "required": ["patient_name", "department", "visit_date", "summary"],
    },
)


class CreateAppointmentArgs(BaseModel):
    patient_name: str
    department: str
    visit_date: dt.date
    summary: str
