"""Pseudonymised token generation and resolution."""

from robopii.models import DetectedPII, TokenMapping


def resolve_or_create_token(entity: DetectedPII) -> TokenMapping:
    """Return an existing token or create one for detected PII."""
    raise NotImplementedError