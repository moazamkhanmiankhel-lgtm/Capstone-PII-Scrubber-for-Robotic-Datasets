"""Controlled retrieval of scrubbed context using tokens.

Retrieval has two levels, and only the second one touches identities:

1. **Context retrieval** (``retrieve_context``, ``build_context``,
   ``recall_entity``) reads the primary store only. It returns what the
   robot previously saw and heard about a token, already de-identified.
   This is what the robot needs to continue a conversation with someone it
   has met before, and it never reveals who the person is.

2. **Identity resolution** (``resolve_identity``, ``forget_identity``) opens the protected vault
   and returns the original value behind a token. It is only allowed for
   actors listed in ``retrieval.authorised_actors`` and needs a stated
   reason. Every attempt, allowed or refused, is written to the vault
   access log.
"""

from dataclasses import dataclass, field
from typing import Any

from robopii.config import Settings, get_config
from robopii.models import TokenMapping
from robopii.storage import (
    delete_protected_mapping,
    find_records_by_token,
    get_protected_mapping,
    get_protected_vault,
)
from robopii.token_manager import get_token_manager, is_valid_token


class AccessDeniedError(PermissionError):
    """Raised when an identity lookup is not authorised."""


@dataclass
class ContextSummary:
    """De-identified history for one token."""

    token: str
    interaction_count: int
    first_seen: str | None
    last_seen: str | None
    related_tokens: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)


def _clean_token(token: str) -> str:
    """Validate a token and return it without surrounding whitespace."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Token cannot be empty.")

    token = token.strip()

    if not is_valid_token(token):
        raise ValueError(
            f"Not a valid RoboPII token: {token!r}. "
            "Tokens look like PERSON_A1B2C3D4E5F6."
        )

    return token


def retrieve_context(
    token: str,
    limit: int | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Return the scrubbed records linked to a token, newest first.

    Reads the primary store only, so no identity is revealed and nothing is
    written to the vault access log.

    Args:
        token: A pseudonymised token such as ``PERSON_A1B2C3D4E5F6``.
        limit: Maximum records to return. Defaults to
            ``retrieval.max_records`` from the settings file.
    """
    token = _clean_token(token)
    settings = settings or get_config()

    if limit is None:
        limit = settings.retrieval.max_records

    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return find_records_by_token(token)[:limit]


def build_context(
    token: str,
    limit: int | None = None,
    settings: Settings | None = None,
) -> ContextSummary:
    """Summarise what the robot knows about a token, without identities.

    ``related_tokens`` lists the other tokens that appeared in the same
    records, for example the email address a person gave, or the other
    people present, in the order they were first seen.
    """
    token = _clean_token(token)
    settings = settings or get_config()

    all_records = find_records_by_token(token)
    records = retrieve_context(token, limit=limit, settings=settings)

    timestamps = sorted(
        record["created_at"]
        for record in all_records
        if record.get("created_at")
    )

    related: dict[str, None] = {}

    for record in reversed(all_records):
        for other in record.get("tokens") or []:
            if other != token:
                related.setdefault(other, None)

    return ContextSummary(
        token=token,
        interaction_count=len(all_records),
        first_seen=timestamps[0] if timestamps else None,
        last_seen=timestamps[-1] if timestamps else None,
        related_tokens=list(related),
        records=records,
    )


def recall_entity(
    pii_type: str,
    value: str,
    limit: int | None = None,
    settings: Settings | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Find past context for someone the robot is meeting again.

    Used when a newly detected value, for example a name heard in a new
    conversation, may belong to someone seen before. The lookup goes
    through the keyed digest in the vault, so no plaintext is searched and
    no new token is created.

    Returns:
        ``(token, records)``, or ``(None, [])`` if the value is unknown.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Value cannot be empty.")

    token = get_token_manager().token_for_value(pii_type, value)

    if token is None:
        return None, []

    return token, retrieve_context(token, limit=limit, settings=settings)


def _record_denied_attempt(token: str, actor: str, reason: str) -> None:
    """Write a refused lookup to the vault access log.

    Uses the vault's own logging so allowed and refused attempts end up in
    the same table and can be audited together.
    """
    vault = get_protected_vault()
    log_access = getattr(vault, "_log_access", None)

    if log_access is None:
        return

    log_access(
        token=token,
        actor=actor or "unknown",
        reason=reason or "unspecified",
        action="denied",
        was_found=False,
    )


def is_authorised(
    actor: str,
    reason: str,
    settings: Settings | None = None,
) -> tuple[bool, str]:
    """Check whether an actor may reveal an identity.

    Returns:
        ``(allowed, explanation)``.
    """
    settings = settings or get_config()
    actor = (actor or "").strip()
    reason = (reason or "").strip()

    if not actor:
        return False, "An actor must be given."

    if actor not in settings.retrieval.authorised_actors:
        return False, f"Actor {actor!r} is not authorised to reveal identities."

    if len(reason) < settings.retrieval.min_reason_length:
        return False, (
            "A reason of at least "
            f"{settings.retrieval.min_reason_length} characters is required."
        )

    return True, "Authorised."


def resolve_identity(
    token: str,
    actor: str,
    reason: str,
    settings: Settings | None = None,
) -> TokenMapping | None:
    """Reveal the original value behind a token, if authorised.

    Every call is logged in the vault access log with the actor and reason:
    ``lookup`` when it was allowed, ``denied`` when it was refused.

    Raises:
        ValueError: the token is malformed.
        AccessDeniedError: the actor or reason is not acceptable.

    Returns:
        The ``TokenMapping``, or ``None`` if the token is not in the vault
        (for example because the person's mapping was deleted).
    """
    token = _clean_token(token)
    allowed, explanation = is_authorised(actor, reason, settings)

    if not allowed:
        _record_denied_attempt(token, actor, reason)
        raise AccessDeniedError(explanation)

    return get_protected_mapping(
        token,
        actor=actor.strip(),
        reason=reason.strip(),
    )


def forget_identity(
    token: str,
    actor: str,
    reason: str,
    settings: Settings | None = None,
) -> bool:
    """Delete the identity behind a token, if authorised.

    The scrubbed records stay in the primary store but can no longer be
    linked to a person, and the person will get a new token if met again.

    Raises:
        ValueError: the token is malformed.
        AccessDeniedError: the actor or reason is not acceptable.

    Returns:
        True when a mapping was removed.
    """
    token = _clean_token(token)
    allowed, explanation = is_authorised(actor, reason, settings)

    if not allowed:
        _record_denied_attempt(token, actor, reason)
        raise AccessDeniedError(explanation)

    return delete_protected_mapping(
        token,
        actor=actor.strip(),
        reason=reason.strip(),
    )
