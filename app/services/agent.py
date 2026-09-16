"""The Agent: the single orchestrator behind POST /chat.

Flow per message (matches README's request-flow diagram):
    guardrails -> load memory -> LLM (structured turn analysis) ->
    sync issue state -> [tool call -> Appointment Service -> Postgres ->
    tool result -> LLM] for any issue that's ready -> persist reply.

The Agent never imports a concrete LLM provider — only `LLMService` — and
never runs raw SQL; all persistence goes through app.services.memory /
appointment_service.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from functools import lru_cache
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.issue import Issue, IssueStatus
from app.models.message import MessageRole
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.schemas.analysis import TurnAnalysis
from app.services import appointment_service, guardrails, memory, observability, summary_service
from app.services.llm.base import LLMMessage, Role
from app.tools.appointments import CREATE_APPOINTMENT_TOOL, CreateAppointmentArgs
from app.services.llm.service import LLMOutputError, LLMService, LLMUnavailableError

logger = logging.getLogger("aceso.agent")

FALLBACK_REPLY = (
    "Sorry, I'm having trouble processing that right now. Could you rephrase, or try again in a moment?"
)


@lru_cache
def get_llm_service() -> LLMService:
    return LLMService(get_settings())


def _parse_iso_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_future_date(value: Optional[str]) -> tuple[Optional[dt.date], bool]:
    """Returns (date, is_past). A date the model produced that's already
    behind today is never usable for booking/rescheduling — the LLM has no
    reliable built-in sense of "today" and can mis-resolve a year-less date
    (e.g. "12 sep") to one already in the past."""
    parsed = _parse_iso_date(value)
    if parsed is None:
        return None, False
    return parsed, parsed < dt.date.today()


def _missing_fields(issue: Issue) -> list[str]:
    missing = []
    if not issue.symptoms:
        missing.append("symptoms")
    if not issue.duration:
        missing.append("duration")
    if not issue.severity:
        missing.append("severity")
    if not issue.department:
        missing.append("department")
    if not issue.preferred_date:
        missing.append("preferred_date (visit date)")
    return missing


def _describe_known_issue(issue: Issue) -> str:
    missing = _missing_fields(issue)
    missing_text = ", ".join(missing) if missing else "nothing — all required info is already collected"
    booked_note = f" appointment_id={issue.appointment_id!r}" if issue.status == IssueStatus.BOOKED else ""
    return (
        f"- id={issue.id} status={issue.status.value}{booked_note}\n"
        f"  known: symptoms={issue.symptoms}, duration={issue.duration!r}, severity={issue.severity!r}, "
        f"relevant_context={issue.relevant_context}, department={issue.department!r}, "
        f"preferred_date={issue.preferred_date!r}\n"
        f"  still missing: {missing_text}"
    )


class Agent:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def process(
        self, db: Session, user_id: str, conversation_id: Optional[str], message: str
    ) -> tuple[str, str]:
        conversation = memory.get_or_create_conversation(db, user_id, conversation_id)

        guard = guardrails.evaluate_input(message)
        memory.add_message(db, conversation.id, MessageRole.USER, guard.redacted_text)

        if guard.injection_detected:
            reply = guardrails.INJECTION_RESPONSE
            memory.add_message(db, conversation.id, MessageRole.ASSISTANT, reply)
            return conversation.id, reply

        if guard.emergency_detected:
            reply = guardrails.EMERGENCY_RESPONSE
            memory.add_message(db, conversation.id, MessageRole.ASSISTANT, reply)
            return conversation.id, reply

        history = memory.get_history(db, conversation.id)
        # ALL issues, not just open ones — a booked issue must stay
        # referenceable by issue_ref (e.g. "change my ENT appointment date")
        # or the model has no id to reuse and ends up creating a duplicate.
        known_issues = memory.get_all_issues(db, conversation.id)
        llm_messages = self._build_messages(conversation, history, known_issues)

        try:
            analysis, meta = await self.llm.generate_structured(llm_messages, TurnAnalysis)
        except (LLMUnavailableError, LLMOutputError) as e:
            logger.error("agent_turn_analysis_failed conversation=%s error=%s", conversation.id, e)
            memory.add_message(db, conversation.id, MessageRole.ASSISTANT, FALLBACK_REPLY)
            return conversation.id, FALLBACK_REPLY

        observability.record_llm_call(db, conversation.id, meta)

        if analysis.patient_name and not conversation.patient_name:
            conversation.patient_name = analysis.patient_name
            db.commit()

        reply_parts = [analysis.reply] if analysis.reply else []
        for extracted in analysis.issues:
            already_booked = False
            if extracted.issue_ref:
                candidate = db.get(Issue, extracted.issue_ref)
                already_booked = (
                    candidate is not None
                    and candidate.conversation_id == conversation.id
                    and candidate.status == IssueStatus.BOOKED
                    and bool(candidate.appointment_id)
                )

            issue = memory.sync_issue(db, conversation.id, extracted)

            if already_booked:
                # This issue already has a confirmed appointment — never run
                # it through _maybe_book again (which would create a
                # duplicate). Apply any changed field to the existing
                # appointment instead.
                reply = self._maybe_reschedule(db, conversation, issue)
                if reply:
                    reply_parts.append(reply)
                continue

            booking_reply = await self._maybe_book(db, conversation, issue)
            if booking_reply:
                reply_parts.append(booking_reply)

        final_reply = "\n\n".join(part for part in reply_parts if part) or (
            "Could you tell me a bit more about what you're experiencing?"
        )
        memory.add_message(db, conversation.id, MessageRole.ASSISTANT, final_reply)
        return conversation.id, final_reply

    def _build_messages(self, conversation, history, known_issues: list[Issue]) -> list[LLMMessage]:
        messages = [
            LLMMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
            LLMMessage(
                role=Role.SYSTEM,
                content=(
                    f"Today's date is {dt.date.today().isoformat()}. When the patient gives a preferred "
                    "visit date without a year (e.g. \"12 sep\" or \"next Tuesday\"), resolve it to the "
                    "next occurrence of that date on or after today — never a date that has already "
                    "passed. Never output a preferred_date earlier than today."
                ),
            ),
        ]

        if known_issues:
            lines = [_describe_known_issue(issue) for issue in known_issues]
            messages.append(
                LLMMessage(
                    role=Role.SYSTEM,
                    content=(
                        "Known issues so far in this conversation. Each issue lists what is already "
                        "confirmed and what is still missing. Do NOT ask about a field marked known — "
                        "reuse it — unless the patient's latest message clearly contradicts or updates "
                        "it. Only ask about fields listed as still missing. When your update continues "
                        "one of these issues, set issue_ref to its id exactly (do not create a duplicate "
                        "issue for the same concern) — this applies even when status is 'booked': if the "
                        "patient asks to change, reschedule, or update anything about an already-booked "
                        "issue, set issue_ref to that issue's id with the updated field(s) (e.g. a new "
                        "preferred_date). The system updates the existing appointment in place; it never "
                        "creates a second appointment for an issue that already has one:\n" + "\n".join(lines)
                    ),
                )
            )

        if conversation.patient_name:
            messages.append(
                LLMMessage(
                    role=Role.SYSTEM,
                    content=f"The patient's name is already known: {conversation.patient_name}. Do not ask again.",
                )
            )

        for m in history:
            role = Role.ASSISTANT if m.role == MessageRole.ASSISTANT else Role.USER
            messages.append(LLMMessage(role=role, content=m.content))

        return messages

    def _maybe_reschedule(self, db: Session, conversation, issue: Issue) -> Optional[str]:
        """`issue` was already BOOKED before this turn's `sync_issue` call.
        That call may have merged a new preferred_date/department onto it,
        and — since the LLM doesn't always know an issue it's updating is
        already booked — may have flipped `status` back to collecting/
        ready_for_booking. Restore BOOKED unconditionally (this issue is
        never allowed to re-enter the normal booking flow) and push any
        actually-changed field onto the existing Appointment row."""
        issue.status = IssueStatus.BOOKED

        appointment = appointment_service.get_appointment(db, issue.appointment_id)
        if appointment is None:
            db.commit()
            return None

        new_date, is_past = _parse_future_date(issue.preferred_date)
        if is_past:
            # Revert to the appointment's current (still-valid) date rather
            # than leaving a stale past date sitting on the issue.
            issue.preferred_date = appointment.visit_date.isoformat()
            db.commit()
            return (
                f"That date ({new_date.isoformat()}) has already passed. "
                "Could you give me a date that's today or later?"
            )
        db.commit()

        changed = appointment_service.reschedule_appointment(
            db,
            appointment=appointment,
            visit_date=new_date,
            department=issue.department,
            notify_email=conversation.user.email,
        )
        if not changed:
            return None
        return f"Your {appointment.department} appointment has been rescheduled to {appointment.visit_date.isoformat()}."

    async def _maybe_book(self, db: Session, conversation, issue: Issue) -> Optional[str]:
        visit_date, is_past = _parse_future_date(issue.preferred_date)
        if is_past:
            # Never book a date that's already gone — clear it so the issue
            # goes back to genuinely "missing a date" rather than silently
            # re-tripping this same check next turn.
            stale_date = visit_date
            issue.preferred_date = None
            db.commit()
            return (
                f"That date ({stale_date.isoformat()}) has already passed. "
                "Could you give me a date that's today or later?"
            )

        ready = (
            issue.status == IssueStatus.READY_FOR_BOOKING
            and bool(issue.department)
            and visit_date is not None
            and bool(conversation.patient_name)
            and not issue.appointment_id
        )
        if not ready:
            return None
        return await self._book_issue(db, conversation, issue, visit_date)

    async def _book_issue(self, db: Session, conversation, issue: Issue, visit_date: dt.date) -> str:
        summary_obj = summary_service.build_doctor_summary(issue)
        summary_text = summary_service.build_appointment_summary_text(summary_obj)

        tool_prompt = LLMMessage(
            role=Role.SYSTEM,
            content=(
                f"All information needed to book the {issue.department} appointment is ready: "
                f"patient_name={conversation.patient_name!r}, department={issue.department!r}, "
                f"visit_date={visit_date.isoformat()!r}, summary={summary_text!r}. "
                "Call create_appointment now with exactly these values."
            ),
        )

        call = None
        try:
            tool_result, meta = await self.llm.generate_with_tools(
                [tool_prompt], [CREATE_APPOINTMENT_TOOL], tool_choice="required"
            )
            observability.record_llm_call(db, conversation.id, meta)
            call = next((c for c in tool_result.tool_calls if c.name == "create_appointment"), None)
        except LLMUnavailableError as e:
            logger.error("booking_tool_call_failed issue=%s error=%s", issue.id, e)

        if call is not None:
            try:
                args = CreateAppointmentArgs.model_validate(call.arguments)
            except ValidationError:
                args = CreateAppointmentArgs(
                    patient_name=conversation.patient_name,
                    department=issue.department,
                    visit_date=visit_date,
                    summary=summary_text,
                )
        else:
            # The model/provider didn't return a usable tool call (e.g. a free
            # OpenRouter model without reliable tool support). The backend
            # already verified every required field, so book deterministically
            # rather than blocking the patient on an LLM quirk.
            args = CreateAppointmentArgs(
                patient_name=conversation.patient_name,
                department=issue.department,
                visit_date=visit_date,
                summary=summary_text,
            )

        appointment, created = appointment_service.book_issue_appointment(
            db,
            issue=issue,
            patient_name=args.patient_name,
            department=args.department,
            visit_date=args.visit_date,
            summary=args.summary,
            doctor_summary=summary_obj.model_dump(),
            notify_email=conversation.user.email,
        )

        if not created:
            # A concurrent request/retry already booked this exact issue —
            # confirm using the appointment that actually exists rather than
            # creating (or emailing about) a duplicate.
            return f"Your {appointment.department} appointment has been booked for {appointment.visit_date.isoformat()}."

        if call is not None:
            followup = [
                tool_prompt,
                LLMMessage(role=Role.ASSISTANT, content="", tool_calls=[call]),
                LLMMessage(
                    role=Role.TOOL,
                    tool_call_id=call.id,
                    name="create_appointment",
                    content=json.dumps({"appointment_id": appointment.appointment_id, "status": "booked"}),
                ),
            ]
            try:
                final, meta2 = await self.llm.generate(followup)
                observability.record_llm_call(db, conversation.id, meta2)
                if final.content:
                    return final.content
            except LLMUnavailableError:
                pass

        return f"Your {issue.department} appointment has been booked for {visit_date.isoformat()}."


@lru_cache
def get_agent() -> Agent:
    return Agent(get_llm_service())
