"""Text PII detection and redaction."""

import re
from functools import lru_cache

import spacy

from robopii.models import DetectedPII, TextScrubResult


# spaCy model used for name and place detection. The small model misses
# names and mislabels them as ORG or FAC, which means they are not redacted.
SPACY_MODEL = "en_core_web_md"

# spaCy entity labels treated as PII, mapped to our own type names.
ENTITY_LABELS = {
    "PERSON": "PERSON",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
}

# Typed email addresses.
EMAIL_PATTERN = re.compile(
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
)

# Spoken email addresses. Covers both "jane dot smith at example dot com"
# and the transcribed form "jane.smith at example.com", where the dots
# survived as punctuation but the at sign did not.
SPOKEN_EMAIL_PATTERN = re.compile(
    r"\b[\w]+(?:(?:\s+dot\s+|\.)[\w]+)*\s+at\s+[\w]+(?:\s+dot\s+|\.)(?:com|org|net|edu|gov|au|io|co)\b",
    re.IGNORECASE,
)

# Email addresses whos @ sign did not survive transcription.
# Email addresses whose at sign did not survive transcription. Matches any
# token ending in a common domain suffix, since that is the only part that
# reliably survives. This over-redacts ordinary web addresses, which is the
# safer failure given privacy preservation is the primary goal.
DOMAIN_EMAIL_PATTERN = re.compile(
    r"\b[\w.+-]+\.(?:com|org|net|edu|gov|io|co|info)(?:\.(?:au|uk|nz))?\b",
    re.IGNORECASE,
)

# Runs of 8 to 12 digits with any separators. Deliberately loose because
# transcription regroups numbers unpredictably and can drop a digit.
PHONE_PATTERN = re.compile(
    r"\b\d(?:[\s.\-()]*\d){7,11}\b"
)


@lru_cache(maxsize=1)
def _load_model():
    """Load the spaCy model once and reuse it."""
    return spacy.load(SPACY_MODEL)


def _regex_spans(text: str) -> list[tuple[int, int, str]]:
    """Find PII that has a recognisable pattern."""
    spans = []

    for match in EMAIL_PATTERN.finditer(text):
        spans.append((match.start(), match.end(), "EMAIL"))

    for match in SPOKEN_EMAIL_PATTERN.finditer(text):
        spans.append((match.start(), match.end(), "EMAIL"))

    for match in PHONE_PATTERN.finditer(text):
        spans.append((match.start(), match.end(), "PHONE"))

    for match in DOMAIN_EMAIL_PATTERN.finditer(text):
        spans.append((match.start(), match.end(), "EMAIL"))

    return spans


def _entity_spans(text: str) -> list[tuple[int, int, str]]:
    """Find names and places using named entity recognition."""
    document = _load_model()(text)

    return [
        (entity.start_char, entity.end_char, ENTITY_LABELS[entity.label_])
        for entity in document.ents
        if entity.label_ in ENTITY_LABELS
    ]


def _remove_overlaps(
    spans: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Keep the longest span where two detections overlap."""
    ordered = sorted(spans, key=lambda span: (span[0], -(span[1] - span[0])))
    kept: list[tuple[int, int, str]] = []

    for span in ordered:
        if kept and span[0] < kept[-1][1]:
            continue

        kept.append(span)

    return kept


def scrub_text(text: str) -> TextScrubResult:
    """Detect PII and return a scrubbed version of the text."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    if not text.strip():
        return TextScrubResult(scrubbed_text=text, detected_pii=[])

    spans = _remove_overlaps(_regex_spans(text) + _entity_spans(text))

    placeholders: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    detected: list[DetectedPII] = []

    # Number placeholders in the order each value first appears.
    for start, end, pii_type in sorted(spans):
        key = (pii_type, text[start:end].lower())

        if key in placeholders:
            continue

        counts[pii_type] = counts.get(pii_type, 0) + 1
        placeholders[key] = f"[{pii_type}_{counts[pii_type]}]"

        detected.append(
            DetectedPII(
                pii_type=pii_type,
                original_value=text[start:end],
                placeholder=placeholders[key],
            )
        )

    # Replace from the end so earlier positions stay valid.
    scrubbed = text

    for start, end, pii_type in sorted(spans, reverse=True):
        key = (pii_type, text[start:end].lower())
        scrubbed = scrubbed[:start] + placeholders[key] + scrubbed[end:]

    return TextScrubResult(scrubbed_text=scrubbed, detected_pii=detected)