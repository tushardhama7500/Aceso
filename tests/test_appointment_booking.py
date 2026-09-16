"""Tests for `appointment_service.book_issue_appointment`: the single
atomic code path that links an Issue to an Appointment and triggers the
confirmation email — used by both the Agent's tool-call flow and the direct
POST /appointments endpoint.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

from app.models.issue import Issue, IssueStatus
from app.services import appointment_service, memory
from app.services.email_service import EmailService


def _make_ready_issue(db_session, test_user):
    conv = memory.get_or_create_conversation(db_session, test_user.id, None)
    issue = Issue(
        conversation_id=conv.id,
        symptoms=["headache"],
        duration="two weeks",
        department="Neurology",
        preferred_date="2026-10-01",
        status=IssueStatus.READY_FOR_BOOKING,
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    return issue


def test_successful_booking_persists_and_links_issue(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)

    appointment, created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
    )

    assert created is True
    assert appointment.appointment_id.startswith("apt_")
    refreshed = db_session.get(Issue, issue.id)
    assert refreshed.appointment_id == appointment.appointment_id
    assert refreshed.status == IssueStatus.BOOKED


def test_duplicate_booking_attempt_returns_existing_appointment_not_a_new_one(db_session, test_user):
    """Simulates a repeated tool call / frontend retry / request retry for
    the exact same issue — must not create a second Appointment row."""
    issue = _make_ready_issue(db_session, test_user)

    first, first_created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
    )
    second, second_created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
    )

    assert first_created is True
    assert second_created is False
    assert second.appointment_id == first.appointment_id

    all_appointments = appointment_service.list_appointments(db_session)
    assert len(all_appointments) == 1


def test_appointment_remains_persisted_when_email_sending_fails(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)
    failing_email_service = MagicMock(spec=EmailService)
    failing_email_service.send_appointment_confirmation.side_effect = RuntimeError("SMTP is down")

    # Even a raising EmailService (not just one that returns False) must
    # never propagate out of book_issue_appointment or affect the booking.
    appointment, created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
        notify_email="john@example.com",
        email_service=failing_email_service,
    )

    assert created is True
    assert appointment is not None
    persisted = appointment_service.list_appointments(db_session)
    assert len(persisted) == 1
    refreshed_issue = db_session.get(Issue, issue.id)
    assert refreshed_issue.status == IssueStatus.BOOKED
    assert refreshed_issue.appointment_id == appointment.appointment_id


def test_email_service_is_called_only_after_successful_persistence(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)
    email_service = MagicMock(spec=EmailService)
    email_service.send_appointment_confirmation.return_value = True

    appointment, created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
        notify_email="john@example.com",
        email_service=email_service,
    )

    assert created is True
    email_service.send_appointment_confirmation.assert_called_once_with(
        to_email="john@example.com",
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        appointment_id=appointment.appointment_id,
    )


def test_email_service_not_called_again_on_duplicate_booking_attempt(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)
    email_service = MagicMock(spec=EmailService)
    email_service.send_appointment_confirmation.return_value = True

    appointment_service.book_issue_appointment(
        db_session, issue=issue, patient_name="John Doe", department="Neurology",
        visit_date=dt.date(2026, 10, 1), summary="x", notify_email="john@example.com", email_service=email_service,
    )
    appointment_service.book_issue_appointment(
        db_session, issue=issue, patient_name="John Doe", department="Neurology",
        visit_date=dt.date(2026, 10, 1), summary="x", notify_email="john@example.com", email_service=email_service,
    )

    assert email_service.send_appointment_confirmation.call_count == 1


def test_no_notify_email_means_no_email_attempt(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)
    email_service = MagicMock(spec=EmailService)

    appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="x",
        notify_email=None,
        email_service=email_service,
    )

    email_service.send_appointment_confirmation.assert_not_called()


def _book_appointment(db_session, test_user):
    issue = _make_ready_issue(db_session, test_user)
    appointment, _created = appointment_service.book_issue_appointment(
        db_session,
        issue=issue,
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        summary="Patient reports headache for two weeks.",
    )
    return appointment


def test_reschedule_sends_a_reschedule_notice_when_date_changes(db_session, test_user):
    appointment = _book_appointment(db_session, test_user)
    email_service = MagicMock(spec=EmailService)
    email_service.send_appointment_reschedule_notice.return_value = True

    changed = appointment_service.reschedule_appointment(
        db_session,
        appointment=appointment,
        visit_date=dt.date(2026, 10, 5),
        notify_email="john@example.com",
        email_service=email_service,
    )

    assert changed is True
    assert appointment.visit_date == dt.date(2026, 10, 5)
    email_service.send_appointment_reschedule_notice.assert_called_once_with(
        to_email="john@example.com",
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 5),
        appointment_id=appointment.appointment_id,
    )
    email_service.send_appointment_confirmation.assert_not_called()


def test_reschedule_sends_no_email_when_nothing_actually_changed(db_session, test_user):
    appointment = _book_appointment(db_session, test_user)
    email_service = MagicMock(spec=EmailService)

    changed = appointment_service.reschedule_appointment(
        db_session,
        appointment=appointment,
        visit_date=appointment.visit_date,  # same date — nothing to notify about
        notify_email="john@example.com",
        email_service=email_service,
    )

    assert changed is False
    email_service.send_appointment_reschedule_notice.assert_not_called()


def test_reschedule_with_no_notify_email_sends_nothing(db_session, test_user):
    appointment = _book_appointment(db_session, test_user)
    email_service = MagicMock(spec=EmailService)

    appointment_service.reschedule_appointment(
        db_session, appointment=appointment, visit_date=dt.date(2026, 10, 5), notify_email=None, email_service=email_service
    )

    email_service.send_appointment_reschedule_notice.assert_not_called()


def test_reschedule_persists_even_if_email_service_raises(db_session, test_user):
    appointment = _book_appointment(db_session, test_user)
    failing_email_service = MagicMock(spec=EmailService)
    failing_email_service.send_appointment_reschedule_notice.side_effect = RuntimeError("SMTP is down")

    changed = appointment_service.reschedule_appointment(
        db_session,
        appointment=appointment,
        visit_date=dt.date(2026, 10, 5),
        notify_email="john@example.com",
        email_service=failing_email_service,
    )

    assert changed is True
    refreshed = appointment_service.get_appointment(db_session, appointment.appointment_id)
    assert refreshed.visit_date == dt.date(2026, 10, 5)
