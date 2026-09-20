"""Audio transcription functionality."""

import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from time import perf_counter

import whisper

from robopii.models import TranscriptResult


# Whisper model size. "base" balances speed and accuracy on a laptop.
# "tiny" is faster and less accurate, "small" is slower and better.
MODEL_SIZE = "base"

# Whisper expects 16 kHz mono audio.
SAMPLE_RATE = "16000"


@lru_cache(maxsize=1)
def _load_model():
    """Load the Whisper model once and reuse it."""
    return whisper.load_model(MODEL_SIZE)


def _extract_audio(input_path: Path, output_path: Path) -> None:
    """Pull the audio track out of a media file into a wav file."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-i", str(input_path),
            "-vn",
            "-ac", "1",
            "-ar", SAMPLE_RATE,
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise ValueError(
            f"Could not extract audio from {input_path}: "
            f"{result.stderr.strip()}"
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ValueError(
            f"No audio track found in {input_path}"
        )


def transcribe_audio(input_path: str) -> TranscriptResult:
    """Convert audio from an input file into text."""
    started_at = perf_counter()

    input_file = Path(input_path)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Input file does not exist: {input_file}"
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        audio_file = Path(temp_dir) / "audio.wav"

        _extract_audio(input_file, audio_file)

        model = _load_model()
        result = model.transcribe(str(audio_file))

    return TranscriptResult(
        transcript=result["text"].strip(),
        processing_time_seconds=(perf_counter() - started_at),
    )