"""Internal structured-output contract the LLM must fill in on every turn.

Not exposed via the public API — this is what the Agent asks the LLM to
produce so multi-issue tracking and department recommendation are backed by
validated data instead of free-form text parsing.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedIssue(BaseModel):
    issue_ref: Optional[str] = Field(
        default=None,
        description="The id of an existing issue this update refers to (copy it exactly from the "
        "'Known issues' context). Leave null when this is a newly mentioned, unrelated concern.",
    )
    symptoms: list[str] = Field(default_factory=list, description="Symptoms the patient explicitly reported")
    duration: Optional[str] = Field(default=None, description="How long the symptom has been present, if stated")
    severity: Optional[str] = Field(default=None, description="Severity as described by the patient, if stated")
    relevant_context: list[str] = Field(
        default_factory=list, description="Other short, relevant facts the patient stated about this issue"
    )
    department: Optional[str] = Field(
        default=None, description="Recommended department once enough is known, e.g. ENT, Neurology, Orthopedics"
    )
    status: Literal["collecting", "ready_for_booking"] = "collecting"
    preferred_date: Optional[str] = Field(
        default=None, description="Patient's preferred visit date normalized to ISO format YYYY-MM-DD, if given"
    )


class TurnAnalysis(BaseModel):
    issues: list[ExtractedIssue] = Field(
        default_factory=list, description="All medical issues discussed so far, new or updated this turn"
    )
    reply: str = Field(description="The natural-language message to show the patient next")
    patient_name: Optional[str] = Field(
        default=None, description="The patient's full name, only if they stated it this turn"
    )
