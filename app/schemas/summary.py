from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DoctorSummary(BaseModel):
    """Structured, doctor-facing summary generated after booking.

    Every field must be traceable to something the patient actually said.
    Never contains a diagnosis, medication, test result, or fabricated detail.
    """

    department: str
    chief_concern: str
    duration: Optional[str] = None
    severity: Optional[str] = None
    patient_reported_symptoms: list[str] = Field(default_factory=list)
    relevant_information: list[str] = Field(default_factory=list)
