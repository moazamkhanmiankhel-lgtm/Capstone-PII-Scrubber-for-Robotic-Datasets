"""Separated primary and protected storage."""

from typing import Any


def initialise_databases() -> None:
    """Create the primary store and protected vault."""
    raise NotImplementedError


def save_primary_record(record: dict[str, Any]) -> str:
    """Save scrubbed data and return its record ID."""
    raise NotImplementedError


def save_protected_mapping(mapping: dict[str, Any]) -> None:
    """Save sensitive token mappings in the protected vault."""
    raise NotImplementedError


def find_records_by_token(token: str) -> list[dict[str, Any]]:
    """Find scrubbed primary records associated with a token."""
    raise NotImplementedError