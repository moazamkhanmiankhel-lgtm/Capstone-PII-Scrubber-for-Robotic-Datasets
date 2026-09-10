"""Text PII detection and redaction."""

from robopii.models import TextScrubResult


def scrub_text(text: str) -> TextScrubResult:
    """Detect PII and return a scrubbed version of the text."""
    raise NotImplementedError