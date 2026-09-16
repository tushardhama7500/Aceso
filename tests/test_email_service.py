"""Tests for EmailService's SMTP integration: correct message shape, the
minimal (non-medical) email content, and that the password is never logged.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib

from app.config import Settings
from app.services.email_service import EmailService


def _settings(**overrides) -> Settings:
    defaults = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_username": "aceso.notifications@gmail.com",
        "smtp_password": "test_app_password_1234",
        "smtp_from_email": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class _FakeSMTP:
    """Stands in for smtplib.SMTP as a context manager, recording calls."""

    created: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list = []
        _FakeSMTP.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send_message", message))


def _install_fake_smtp(monkeypatch, cls=_FakeSMTP):
    cls.created = []
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", cls)
    return cls


def test_disabled_when_smtp_not_configured(monkeypatch):
    service = EmailService(Settings(smtp_username="", smtp_password=""))
    fake_cls = _install_fake_smtp(monkeypatch)

    result = service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_123",
    )

    assert result is False
    assert fake_cls.created == []


def test_sends_to_correct_recipient_and_fields(monkeypatch):
    service = EmailService(_settings())
    fake_cls = _install_fake_smtp(monkeypatch)

    result = service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_abc123",
    )

    assert result is True
    assert len(fake_cls.created) == 1
    server = fake_cls.created[0]
    assert server.host == "smtp.gmail.com"
    assert server.port == 587
    assert ("starttls",) in server.calls

    login_call = next(c for c in server.calls if c[0] == "login")
    assert login_call[1] == "aceso.notifications@gmail.com"
    assert login_call[2] == "test_app_password_1234"

    sent_message = next(c[1] for c in server.calls if c[0] == "send_message")
    assert sent_message["To"] == "patient@example.com"
    assert "ENT" in sent_message["Subject"]
    html = sent_message.get_body(preferencelist=("html",)).get_content()
    assert "ENT" in html
    assert "2026-10-01" in html
    assert "apt_abc123" in html


def test_no_tls_when_smtp_use_tls_is_false(monkeypatch):
    service = EmailService(_settings(smtp_use_tls=False))
    fake_cls = _install_fake_smtp(monkeypatch)

    service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_123",
    )

    server = fake_cls.created[0]
    assert ("starttls",) not in server.calls


def test_from_email_defaults_to_smtp_username(monkeypatch):
    service = EmailService(_settings(smtp_from_email=""))
    fake_cls = _install_fake_smtp(monkeypatch)

    service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_123",
    )

    sent_message = next(c[1] for c in fake_cls.created[0].calls if c[0] == "send_message")
    assert "aceso.notifications@gmail.com" in sent_message["From"]


def test_email_does_not_include_full_medical_summary(monkeypatch):
    service = EmailService(_settings())
    fake_cls = _install_fake_smtp(monkeypatch)

    # A doctor_summary/full clinical description is never passed to
    # EmailService at all — its signature has no such parameter — so the
    # rendered email can only ever contain department/date/ID/patient name.
    service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="Neurology",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_xyz",
    )

    sent_message = next(c[1] for c in fake_cls.created[0].calls if c[0] == "send_message")
    html = sent_message.get_body(preferencelist=("html",)).get_content()
    for clinical_term in ("chief_concern", "symptoms", "severity", "relevant_information", "doctor_summary"):
        assert clinical_term not in html


def test_send_failure_is_logged_and_returns_false_without_raising(monkeypatch):
    service = EmailService(_settings())

    class _BoomSMTP(_FakeSMTP):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    _install_fake_smtp(monkeypatch, _BoomSMTP)

    result = service.send_appointment_confirmation(
        to_email="patient@example.com",
        patient_name="John Doe",
        department="ENT",
        visit_date=dt.date(2026, 10, 1),
        appointment_id="apt_123",
    )

    assert result is False


def test_password_never_appears_in_logs(monkeypatch, caplog):
    secret_password = "super_secret_app_password_do_not_leak"
    service = EmailService(_settings(smtp_password=secret_password))

    class _BoomSMTP(_FakeSMTP):
        def login(self, username, password):
            # A realistic SMTP auth failure — its message never echoes back
            # the password that was tried. This checks that OUR logging
            # calls never separately reference it, not that the library
            # happens not to.
            raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    _install_fake_smtp(monkeypatch, _BoomSMTP)

    with caplog.at_level(logging.WARNING, logger="aceso.email"):
        service.send_appointment_confirmation(
            to_email="patient@example.com",
            patient_name="John Doe",
            department="ENT",
            visit_date=dt.date(2026, 10, 1),
            appointment_id="apt_123",
        )

    assert secret_password not in caplog.text


def test_source_never_logs_the_password_value():  # noqa: D103
    import inspect

    from app.services import email_service

    source = inspect.getsource(email_service)
    # Every log call in this module must reference appointment_id/error/etc,
    # never the raw settings.smtp_password value.
    for line in source.splitlines():
        if "logger." in line:
            assert "smtp_password" not in line, f"log statement references the password directly: {line!r}"
