"""Controlled retreieval of scrubbed records using tokens."""

from typing import Any

from robopii.storage import find_records_by_token


def retrieve_context(token: str) -> list[dict[str, Any]]:
    
    if not token.strip():
        raise ValueError("Token cannot be empty.")

    return find_records_by_token(token)

