"""Shared data structures used by all RoboPII components."""

from dataclasses import dataclass, field


@dataclass
class DetectedPII:
    """PII detected temporarily during processing."""

    pii_type: str
    original_value: str
    placeholder: str


@dataclass
class VisualScrubResult:
    """Result returned by the visual scrubber."""

    output_path: str
    faces_detected: int
    processing_time_seconds: float
    tokens: list[str] = field(default_factory=list)


@dataclass
class TranscriptResult:
    """Result returned by the audio transcription component."""

    transcript: str
    processing_time_seconds: float


@dataclass
class TextScrubResult:
    """Result returned by the text scrubber."""

    scrubbed_text: str
    detected_pii: list[DetectedPII] = field(default_factory=list)


@dataclass
class TokenMapping:
    """Mapping stored in the protected identity vault."""

    token: str
    pii_type: str
    original_value: str


@dataclass
class ProcessingResult:
    """Final result returned by the complete pipeline."""

    record_id: str
    scrubbed_media_path: str | None
    scrubbed_transcript: str | None
    tokens: list[str] = field(default_factory=list)
