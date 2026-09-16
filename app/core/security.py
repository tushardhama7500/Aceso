"""Password hashing (bcrypt) and JWT access tokens (PyJWT).

Kept as a small, dependency-light module rather than passlib's
`CryptContext` — bcrypt directly is the standard, well-maintained choice and
avoids passlib's known compatibility issues with recent bcrypt releases.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import bcrypt
import jwt

from app.config import Settings

ALGORITHM_ALLOWLIST = {"HS256", "HS384", "HS512"}


class TokenError(Exception):
    """Raised for any invalid, expired, or malformed access token."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/foreign hash format — never a match, never a crash.
        return False


def _require_secret(settings: Settings) -> str:
    if not settings.jwt_secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not configured. Set it in .env before issuing or verifying tokens."
        )
    return settings.jwt_secret_key


def create_access_token(*, subject: str, settings: Settings) -> str:
    """`subject` is the user id (never the email/password) encoded as the JWT `sub` claim."""
    secret = _require_secret(settings)
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "iat": now, "exp": expires_at}
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings) -> str:
    """Returns the user id (`sub` claim). Raises TokenError on anything invalid."""
    secret = _require_secret(settings)
    if settings.jwt_algorithm not in ALGORITHM_ALLOWLIST:
        raise TokenError(f"Unsupported JWT algorithm: {settings.jwt_algorithm}")
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("Token has expired") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("Invalid token") from e

    subject: Optional[str] = payload.get("sub")
    if not subject:
        raise TokenError("Token missing subject claim")
    return subject
