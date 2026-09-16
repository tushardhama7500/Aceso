"""EmailService: appointment confirmation/reschedule emails via SMTP.

Uses Python's stdlib `smtplib`/`email` — no extra dependency. Works with a
Gmail App Password (Google Account -> Security -> 2-Step Verification ->
App passwords) or any other SMTP account/provider via SMTP_HOST/SMTP_PORT.

Fire-and-forget from the caller's perspective: neither public method ever
raises. A failed send is logged and swallowed — the booking/reschedule it
notifies about has already been committed to Postgres by the time this
runs, and email delivery failing must never undo that write.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
from email.message import EmailMessage
from functools import lru_cache

from app.config import Settings, get_settings

logger = logging.getLogger("aceso.email")


class EmailService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.smtp_username and self.settings.smtp_password)

    def send_appointment_confirmation(
        self,
        *,
        to_email: str,
        patient_name: str,
        department: str,
        visit_date: dt.date,
        appointment_id: str,
    ) -> bool:
        return self._send(
            to_email=to_email,
            subject=f"Your {department} appointment is confirmed",
            intro="Your appointment has been confirmed:",
            patient_name=patient_name,
            department=department,
            visit_date=visit_date,
            appointment_id=appointment_id,
        )

    def send_appointment_reschedule_notice(
        self,
        *,
        to_email: str,
        patient_name: str,
        department: str,
        visit_date: dt.date,
        appointment_id: str,
    ) -> bool:
        return self._send(
            to_email=to_email,
            subject=f"Your {department} appointment has been rescheduled",
            intro="Your appointment has been rescheduled:",
            patient_name=patient_name,
            department=department,
            visit_date=visit_date,
            appointment_id=appointment_id,
        )

    def _send(
        self,
        *,
        to_email: str,
        subject: str,
        intro: str,
        patient_name: str,
        department: str,
        visit_date: dt.date,
        appointment_id: str,
    ) -> bool:
        if not self.enabled:
            logger.info("email_skipped reason=smtp_not_configured appointment_id=%s", appointment_id)
            return False

        from_email = self.settings.smtp_from_email or self.settings.smtp_username

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"Aceso <{from_email}>"
        message["To"] = to_email
        message.set_content(
            _text(
                intro=intro,
                patient_name=patient_name,
                department=department,
                visit_date=visit_date,
                appointment_id=appointment_id,
            )
        )
        message.add_alternative(
            _html(
                intro=intro,
                patient_name=patient_name,
                department=department,
                visit_date=visit_date,
                appointment_id=appointment_id,
            ),
            subtype="html",
        )

        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as server:
                if self.settings.smtp_use_tls:
                    server.starttls()
                server.login(self.settings.smtp_username, self.settings.smtp_password)
                server.send_message(message)
        except Exception as e:  # noqa: BLE001 — never let email failure propagate
            logger.warning("email_send_failed appointment_id=%s error=%s", appointment_id, e)
            return False

        logger.info("email_sent appointment_id=%s", appointment_id)
        return True


def _text(*, intro: str, patient_name: str, department: str, visit_date: dt.date, appointment_id: str) -> str:
    greeting = f"Hi {patient_name}," if patient_name else "Hello,"
    return (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        f"Department: {department}\n"
        f"Date: {visit_date.isoformat()}\n"
        f"Appointment ID: {appointment_id}\n\n"
        "Please arrive a few minutes early. Contact the clinic directly if you need to make further changes.\n\n"
        "— Aceso, Intelligent pathways to care."
    )


def _html(*, intro: str, patient_name: str, department: str, visit_date: dt.date, appointment_id: str) -> str:
    greeting = f"Hi {patient_name}," if patient_name else "Hello,"
    # Deliberately minimal: department, date, and ID only — never the
    # doctor-facing medical summary.
    return f"""\
<div style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto;">
  <h2 style="color:#0b5b54; margin-bottom:0;">Aceso</h2>
  <p style="color:#5c6b67; font-style: italic; margin-top:2px;">Intelligent pathways to care.</p>
  <p>{greeting}</p>
  <p>{intro}</p>
  <table style="width:100%; border-collapse: collapse; margin: 16px 0;">
    <tr><td style="padding:6px 0; color:#5c6b67;">Department</td>
        <td style="padding:6px 0; font-weight:600;">{department}</td></tr>
    <tr><td style="padding:6px 0; color:#5c6b67;">Date</td>
        <td style="padding:6px 0; font-weight:600;">{visit_date.isoformat()}</td></tr>
    <tr><td style="padding:6px 0; color:#5c6b67;">Appointment ID</td>
        <td style="padding:6px 0; font-family: monospace;">{appointment_id}</td></tr>
  </table>
  <p style="color:#5c6b67; font-size:0.9rem;">
    Please arrive a few minutes early. Contact the clinic directly if you need to make further changes.
  </p>
</div>
"""


@lru_cache
def get_email_service() -> EmailService:
    return EmailService(get_settings())
