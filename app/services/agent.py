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


class Agent:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def process(self, db: Session, conversation_id: Optional[str], message: str) -> tuple[str, str]:
        conversation = memory.get_or_create_conversation(db, conversation_id)

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
        open_issues = memory.get_open_issues(db, conversation.id)
        llm_messages = self._build_messages(conversation, history, open_issues)

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

        synced_issues = [memory.sync_issue(db, conversation.id, extracted) for extracted in analysis.issues]

        reply_parts = [analysis.reply] if analysis.reply else []
        for issue in synced_issues:
            booking_reply = await self._maybe_book(db, conversation, issue)
            if booking_reply:
                reply_parts.append(booking_reply)

        final_reply = "\n\n".join(part for part in reply_parts if part) or (
            "Could you tell me a bit more about what you're experiencing?"
        )
        memory.add_message(db, conversation.id, MessageRole.ASSISTANT, final_reply)
        return conversation.id, final_reply

    def _build_messages(self, conversation, history, open_issues: list[Issue]) -> list[LLMMessage]:
        messages = [LLMMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT)]

        if open_issues:
            lines = [
                f"- id={issue.id} symptoms={issue.symptoms} duration={issue.duration} "
                f"severity={issue.severity} department={issue.department} status={issue.status.value} "
                f"preferred_date={issue.preferred_date}"
                for issue in open_issues
            ]
            messages.append(
                LLMMessage(
                    role=Role.SYSTEM,
                    content="Known issues so far in this conversation (reference by id via issue_ref when "
                    "updating one — do not create a duplicate):\n" + "\n".join(lines),
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

    async def _maybe_book(self, db: Session, conversation, issue: Issue) -> Optional[str]:
        visit_date = _parse_iso_date(issue.preferred_date)
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

        appointment = appointment_service.create_appointment(
            db,
            patient_name=args.patient_name,
            department=args.department,
            visit_date=args.visit_date,
            summary=args.summary,
            doctor_summary=summary_obj.model_dump(),
        )

        issue.status = IssueStatus.BOOKED
        issue.appointment_id = appointment.appointment_id
        issue.doctor_summary = summary_obj.model_dump()
        db.commit()

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
