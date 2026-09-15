"""Deterministic safety layer applied to every user message BEFORE it reaches
the LLM. Kept intentionally simple (regex/keyword based) per the assignment's
"do not overengineer" guidance — see README "Guardrails" for rationale and
known limitations.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

# --- PII redaction -----------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
# Matches common phone formats (555-123-4567, (555) 123-4567, +1 555-123-4567,
# 5551234567) while avoiding false positives on plain dates like 2026-09-20,
# which use a 4-2-2 digit grouping rather than phone-style 3-3-4 groupings.
_PHONE_RE = re.compile(
    r"(?<!\d)(\+\d{1,2}[-.\s]?)?(\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

PII_PLACEHOLDER_EMAIL = "[EMAIL]"
PII_PLACEHOLDER_PHONE = "[PHONE]"


def redact_pii(text: str) -> tuple[str, bool]:
    redacted = _EMAIL_RE.sub(PII_PLACEHOLDER_EMAIL, text)
    redacted, n_phone = _PHONE_RE.subn(PII_PLACEHOLDER_PHONE, redacted)
    detected = redacted != text
    return redacted, detected


# --- Prompt injection detection -----------------------------------------

_INJECTION_PATTERNS = [
    r"ignore (all|any )?(the )?(previous|prior|above|earlier) instructions",
    r"ignore (your )?(system prompt|safety rules|guardrails|instructions)",
    r"disregard (all |any )?(previous|prior|above) (instructions|rules)",
    r"reveal (your |the )?(system prompt|hidden|developer)",
    r"show me (your |the )?(system prompt|developer|instructions|hidden)",
    r"developer (instructions|message|prompt)",
    r"what (is|are) your (system prompt|instructions|hidden instructions)",
    r"repeat (the|your) (system prompt|instructions|words above)",
    r"you are (now )?(in )?(dan|jailbreak|developer mode)",
    r"bypass (your )?(safety|guardrails|restrictions)",
    r"act as (if you have no|an unrestricted)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def detect_prompt_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


# --- Emergency detection --------------------------------------------------

_EMERGENCY_KEYWORDS = [
    "chest pain",
    "can't breathe",
    "cannot breathe",
    "difficulty breathing",
    "shortness of breath",
    "severe bleeding",
    "heavy bleeding",
    "suicidal",
    "kill myself",
    "unconscious",
    "not breathing",
    "stroke",
    "face drooping",
    "slurred speech",
    "anaphylaxis",
    "severe allergic reaction",
    "heart attack",
    "seizure",
    "overdose",
    "poisoning",
    "severe burn",
    "choking",
]


def detect_emergency(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _EMERGENCY_KEYWORDS)


# --- Canned, deterministic responses (no LLM call needed) ----------------

INJECTION_RESPONSE = (
    "I can't share internal instructions or system details. Let's get back to your health "
    "concern — what symptoms are you experiencing?"
)

EMERGENCY_RESPONSE = (
    "This sounds like it could be a medical emergency. Please call your local emergency number "
    "or go to the nearest emergency room right away. I'm not able to help with emergencies here."
)


class GuardrailResult(BaseModel):
    redacted_text: str
    pii_detected: bool
    injection_detected: bool
    emergency_detected: bool


def evaluate_input(text: str) -> GuardrailResult:
    redacted, pii_detected = redact_pii(text)
    return GuardrailResult(
        redacted_text=redacted,
        pii_detected=pii_detected,
        injection_detected=detect_prompt_injection(text),
        emergency_detected=detect_emergency(text),
    )
