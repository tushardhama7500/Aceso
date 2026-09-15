"""Doctor-facing summary generation.

Deliberately NOT a second free-form LLM call: the summary is assembled
directly from the same Issue fields that were already extracted (and
Pydantic-validated) during the conversation. This gives a hard guarantee —
not just a prompt instruction — that the summary can never contain anything
the patient didn't actually say, since there is no generative step left that
could fabricate content. See README "Doctor-facing summary".
"""
from __future__ import annotations

from app.models.issue import Issue
from app.schemas.summary import DoctorSummary


def build_doctor_summary(issue: Issue) -> DoctorSummary:
    chief_concern = issue.symptoms[0] if issue.symptoms else "Not specified"
    return DoctorSummary(
        department=issue.department or "Not specified",
        chief_concern=chief_concern,
        duration=issue.duration,
        severity=issue.severity,
        patient_reported_symptoms=list(issue.symptoms or []),
        relevant_information=list(issue.relevant_context or []),
    )


def build_appointment_summary_text(summary: DoctorSummary) -> str:
    text = f"Patient reports {summary.chief_concern.lower()}"
    if summary.duration:
        text += f" for {summary.duration}"
    return text + "."
