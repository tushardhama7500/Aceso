"""User persistence and authentication — the only code path allowed to read
or write `User` rows or compare passwords."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User


class EmailAlreadyRegisteredError(Exception):
    """Raised on registration with an email that already has an account."""


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    stmt = select(User).where(User.email == email.lower())
    return db.scalars(stmt).first()


def create_user(db: Session, *, email: str, password: str) -> User:
    email = email.lower()
    if get_user_by_email(db, email) is not None:
        raise EmailAlreadyRegisteredError(f"Email already registered: {email}")

    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as e:
        # Guards the race between the check above and the insert (unique
        # constraint on users.email is the real source of truth).
        db.rollback()
        raise EmailAlreadyRegisteredError(f"Email already registered: {email}") from e
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
