"""Tests for registration, login, and the get_current_user JWT dependency."""
from __future__ import annotations

import datetime as dt

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core.security import TokenError, create_access_token, decode_access_token, hash_password, verify_password
from app.db import get_db
from app.main import app
from app.services import user_service

TEST_SETTINGS = Settings(jwt_secret_key="test-secret-key-for-auth-tests", jwt_algorithm="HS256")


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- Registration -----------------------------------------------------------


def test_register_success(client):
    resp = client.post("/auth/register", json={"email": "new@example.com", "password": "supersecret123"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["id"].startswith("user_")
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


def test_register_duplicate_email_rejected(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "supersecret123"})

    resp = client.post("/auth/register", json={"email": "dup@example.com", "password": "anotherpassword"})

    assert resp.status_code == 409


def test_register_invalid_email_rejected(client):
    resp = client.post("/auth/register", json={"email": "not-an-email", "password": "supersecret123"})

    assert resp.status_code == 422


def test_register_short_password_rejected(client):
    resp = client.post("/auth/register", json={"email": "shortpw@example.com", "password": "short"})

    assert resp.status_code == 422


def test_password_is_hashed_not_stored_plaintext(client, db_session):
    client.post("/auth/register", json={"email": "hash@example.com", "password": "supersecret123"})

    user = user_service.get_user_by_email(db_session, "hash@example.com")

    assert user.password_hash != "supersecret123"
    assert user.password_hash.startswith("$2b$")  # bcrypt hash marker
    assert verify_password("supersecret123", user.password_hash)


# --- Login --------------------------------------------------------------


def test_login_success(client):
    client.post("/auth/register", json={"email": "login@example.com", "password": "supersecret123"})

    resp = client.post("/auth/login", json={"email": "login@example.com", "password": "supersecret123"})

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_login_incorrect_password_rejected(client):
    client.post("/auth/register", json={"email": "wrongpw@example.com", "password": "supersecret123"})

    resp = client.post("/auth/login", json={"email": "wrongpw@example.com", "password": "not-the-password"})

    assert resp.status_code == 401


def test_login_unknown_email_rejected(client):
    resp = client.post("/auth/login", json={"email": "ghost@example.com", "password": "whatever123"})

    assert resp.status_code == 401


# --- get_current_user / protected endpoints ------------------------------


def test_protected_endpoint_without_token_rejected(client):
    resp = client.get("/appointments")

    assert resp.status_code == 401


def test_protected_endpoint_with_invalid_token_rejected(client):
    resp = client.get("/appointments", headers=_auth_headers("not-a-real-token"))

    assert resp.status_code == 401


def test_protected_endpoint_with_expired_token_rejected(client):
    now = dt.datetime.now(dt.timezone.utc)
    expired_token = jwt.encode(
        {"sub": "user_doesnotmatter", "iat": now - dt.timedelta(minutes=10), "exp": now - dt.timedelta(minutes=1)},
        TEST_SETTINGS.jwt_secret_key,
        algorithm=TEST_SETTINGS.jwt_algorithm,
    )

    resp = client.get("/appointments", headers=_auth_headers(expired_token))

    assert resp.status_code == 401


def test_protected_endpoint_with_valid_token_succeeds(client):
    reg = client.post("/auth/register", json={"email": "valid@example.com", "password": "supersecret123"})
    token = reg.json()["access_token"]

    resp = client.get("/appointments", headers=_auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == []


def test_token_for_deleted_user_is_rejected(client, db_session):
    reg = client.post("/auth/register", json={"email": "deleteme@example.com", "password": "supersecret123"})
    token = reg.json()["access_token"]
    user = user_service.get_user_by_email(db_session, "deleteme@example.com")
    db_session.delete(user)
    db_session.commit()

    resp = client.get("/appointments", headers=_auth_headers(token))

    assert resp.status_code == 401


# --- app/core/security.py unit tests -------------------------------------


def test_hash_password_uses_a_fresh_salt_each_time():
    h1 = hash_password("samepassword")
    h2 = hash_password("samepassword")

    assert h1 != h2
    assert verify_password("samepassword", h1)
    assert verify_password("samepassword", h2)


def test_verify_password_rejects_wrong_password():
    h = hash_password("correct-password")

    assert verify_password("wrong-password", h) is False


def test_access_token_roundtrip():
    token = create_access_token(subject="user_abc123", settings=TEST_SETTINGS)

    assert decode_access_token(token, settings=TEST_SETTINGS) == "user_abc123"


def test_decode_rejects_tampered_token():
    token = create_access_token(subject="user_abc123", settings=TEST_SETTINGS) + "tampered"

    with pytest.raises(TokenError):
        decode_access_token(token, settings=TEST_SETTINGS)


def test_decode_rejects_token_signed_with_a_different_secret():
    token = create_access_token(subject="user_abc123", settings=TEST_SETTINGS)
    other_settings = Settings(jwt_secret_key="a-completely-different-secret")

    with pytest.raises(TokenError):
        decode_access_token(token, settings=other_settings)


def test_create_access_token_requires_a_configured_secret():
    with pytest.raises(RuntimeError):
        create_access_token(subject="user_abc123", settings=Settings(jwt_secret_key=""))
