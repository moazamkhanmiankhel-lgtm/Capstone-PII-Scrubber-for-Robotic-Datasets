"""Fake components used by the pipeline and retrieval tests.

These stand in for the model-based components (face detection, Whisper,
spaCy) so the integration tests run quickly on any machine. The token
manager and storage are never faked.
"""

import re
from dataclasses import replace
from pathlib import Path

from robopii.config import Settings
from robopii.models import (
    DetectedPII,
    TextScrubResult,
    TranscriptResult,
    VisualScrubResult,
)


def with_pipeline(settings: Settings, **changes) -> Settings:
    """Return a copy of settings with some pipeline values changed."""
    return replace(settings, pipeline=replace(settings.pipeline, **changes))


# Fake components ----------------------------------------------------------


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\b\d(?:[\s-]?\d){7,11}\b")


class FakeTextScrubber:
    """Deterministic stand-in for the spaCy text scrubber.

    Detects the names listed in ``names`` plus emails and phone numbers, and
    numbers placeholders the same way the real scrubber does.
    """

    def __init__(self, names=("Jane Smith", "Bob Lee", "Sydney"), types=None):
        self.names = list(names)
        self.types = types or {"Sydney": "LOCATION"}
        self.calls = []

    def __call__(self, text: str) -> TextScrubResult:
        self.calls.append(text)
        spans = []

        for name in self.names:
            for match in re.finditer(re.escape(name), text):
                spans.append((match.start(), match.end(), self.types.get(name, "PERSON")))

        for match in EMAIL.finditer(text):
            spans.append((match.start(), match.end(), "EMAIL"))

        for match in PHONE.finditer(text):
            spans.append((match.start(), match.end(), "PHONE"))

        spans.sort()
        placeholders, counts, detected = {}, {}, []

        for start, end, pii_type in spans:
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

        scrubbed = text

        for start, end, pii_type in sorted(spans, reverse=True):
            key = (pii_type, text[start:end].lower())
            scrubbed = scrubbed[:start] + placeholders[key] + scrubbed[end:]

        return TextScrubResult(scrubbed_text=scrubbed, detected_pii=detected)


class FakeVideoScrubber:
    """Writes one scrubbed and one original frame, like the real scrubber."""

    def __init__(self, faces=2, fail=False):
        self.faces = faces
        self.fail = fail
        self.original_dirs = []

    def __call__(self, input_path, output_path, original_output_dir=None):
        scrubbed_dir = Path(output_path)
        scrubbed_dir.mkdir(parents=True, exist_ok=True)
        (scrubbed_dir / "frame_000001.jpg").write_bytes(b"scrubbed")

        if original_output_dir is not None:
            original_dir = Path(original_output_dir)
            original_dir.mkdir(parents=True, exist_ok=True)
            (original_dir / "frame_000001.jpg").write_bytes(b"original")
            self.original_dirs.append(original_dir)

        if self.fail:
            raise RuntimeError("simulated face detector crash")

        return VisualScrubResult(
            output_path=str(scrubbed_dir),
            faces_detected=self.faces,
            processing_time_seconds=0.25,
        )


class FakeImageScrubber:
    def __call__(self, input_path, output_path):
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"blurred")

        return VisualScrubResult(
            output_path=str(output_file),
            faces_detected=1,
            processing_time_seconds=0.05,
        )


class FakeTranscriber:
    """Returns a fixed transcript, or raises like a video with no audio."""

    def __init__(self, transcript="", no_audio=False):
        self.transcript = transcript
        self.no_audio = no_audio
        self.calls = []

    def __call__(self, input_path):
        self.calls.append(input_path)

        if self.no_audio:
            raise ValueError(f"No audio track found in {input_path}")

        return TranscriptResult(transcript=self.transcript, processing_time_seconds=0.5)
