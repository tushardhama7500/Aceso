"""Prefixed, human-legible ID generators (e.g. conv_ab12cd34)."""
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
