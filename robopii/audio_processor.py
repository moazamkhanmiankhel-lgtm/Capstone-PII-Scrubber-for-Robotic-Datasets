"""Audio transcription functionality."""

from robopii.models import TranscriptResult


def transcribe_audio(input_path: str) -> TranscriptResult:
    """Convert audio from an input file into text."""
    raise NotImplementedError