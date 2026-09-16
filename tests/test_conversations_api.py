"""Tests for the conversation-history endpoints: GET /conversations and
GET /conversations/{id}/messages — listing and replaying a user's own past
conversations, scoped the same way as everything else (never another
user's data, 404 either way).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models.message import MessageRole
from app.services import memory

TEST_SETTINGS = Settings(jwt_secret_key="test-secret-key-for-conversations-tests", jwt_algorithm="HS256")


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


def _register(client, email: str) -> tuple[str, str]:
    resp = client.post("/auth/register", json={"email": email, "password": "supersecret123"})
    body = resp.json()
    return body["user"]["id"], body["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_conversations_is_empty_for_a_new_user(client):
    _, token = _register(client, "empty@example.com")

    resp = client.get("/conversations", headers=_auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == []


def test_conversation_appears_with_first_message_as_title(client, db_session):
    user_id, token = _register(client, "titled@example.com")
    conv = memory.get_or_create_conversation(db_session, user_id, None)
    memory.add_message(db_session, conv.id, MessageRole.USER, "I have a headache")
    memory.add_message(db_session, conv.id, MessageRole.ASSISTANT, "How long has this been going on?")

    resp = client.get("/conversations", headers=_auth_headers(token))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == conv.id
    assert body[0]["title"] == "I have a headache"


def test_long_first_message_is_truncated_for_the_title(client, db_session):
    user_id, token = _register(client, "longtitle@example.com")
    conv = memory.get_or_create_conversation(db_session, user_id, None)
    long_message = "I have been experiencing a persistent headache for several weeks now and it is getting worse"
    memory.add_message(db_session, conv.id, MessageRole.USER, long_message)

    resp = client.get("/conversations", headers=_auth_headers(token))

    title = resp.json()[0]["title"]
    assert len(title) <= 61  # 60 chars + ellipsis
    assert title.endswith("…")


def test_conversations_ordered_most_recently_active_first(client, db_session):
    user_id, token = _register(client, "ordering@example.com")
    conv_a = memory.get_or_create_conversation(db_session, user_id, None)
    memory.add_message(db_session, conv_a.id, MessageRole.USER, "first conversation")
    conv_b = memory.get_or_create_conversation(db_session, user_id, None)
    memory.add_message(db_session, conv_b.id, MessageRole.USER, "second conversation")
    # touch conv_a again — it should now sort ahead of conv_b
    memory.add_message(db_session, conv_a.id, MessageRole.USER, "back to the first one")

    resp = client.get("/conversations", headers=_auth_headers(token))

    ids_in_order = [c["id"] for c in resp.json()]
    assert ids_in_order == [conv_a.id, conv_b.id]


def test_get_conversation_messages_returns_full_history(client, db_session):
    user_id, token = _register(client, "history@example.com")
    conv = memory.get_or_create_conversation(db_session, user_id, None)
    memory.add_message(db_session, conv.id, MessageRole.USER, "I have a headache")
    memory.add_message(db_session, conv.id, MessageRole.ASSISTANT, "How long has this been going on?")

    resp = client.get(f"/conversations/{conv.id}/messages", headers=_auth_headers(token))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0] == {"role": "user", "content": "I have a headache", "created_at": body[0]["created_at"]}
    assert body[1]["role"] == "assistant"


def test_user_b_cannot_list_or_read_user_a_conversation_messages(client, db_session):
    user_a_id, _ = _register(client, "a_conv@example.com")
    _, token_b = _register(client, "b_conv@example.com")
    conv = memory.get_or_create_conversation(db_session, user_a_id, None)
    memory.add_message(db_session, conv.id, MessageRole.USER, "private symptom detail")

    list_resp = client.get("/conversations", headers=_auth_headers(token_b))
    assert list_resp.json() == []

    messages_resp = client.get(f"/conversations/{conv.id}/messages", headers=_auth_headers(token_b))
    assert messages_resp.status_code == 404


def test_conversations_endpoints_require_authentication(client):
    assert client.get("/conversations").status_code == 401
    assert client.get("/conversations/conv_doesnotexist/messages").status_code == 401


def test_get_messages_for_nonexistent_conversation_is_404(client):
    _, token = _register(client, "nonexistent@example.com")

    resp = client.get("/conversations/conv_doesnotexist/messages", headers=_auth_headers(token))

    assert resp.status_code == 404
