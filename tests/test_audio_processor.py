import subprocess
import wave

import pytest

from robopii.audio_processor import transcribe_audio
from robopii.models import TranscriptResult


def _make_silent_wav(path, seconds=1):
    """Create a short silent wav file."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 16000 * seconds)


def _make_spoken_wav(path, phrase):
    """Create a wav file of synthesised speech (macOS only)."""
    aiff_path = path.with_suffix(".aiff")

    subprocess.run(
        ["say", "-o", str(aiff_path), phrase],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(aiff_path), str(path)],
        check=True,
    )


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_audio(str(tmp_path / "does_not_exist.wav"))


def test_file_with_no_audio_raises(tmp_path):
    text_file = tmp_path / "not_audio.txt"
    text_file.write_text("this is not a media file")

    with pytest.raises(ValueError):
        transcribe_audio(str(text_file))


def test_returns_transcript_result(tmp_path):
    wav_path = tmp_path / "silence.wav"
    _make_silent_wav(wav_path)

    result = transcribe_audio(str(wav_path))

    assert isinstance(result, TranscriptResult)
    assert isinstance(result.transcript, str)
    assert result.processing_time_seconds > 0


@pytest.mark.slow
def test_transcribes_known_speech(tmp_path):
    wav_path = tmp_path / "speech.wav"
    _make_spoken_wav(wav_path, "the weather is cold today")

    result = transcribe_audio(str(wav_path))

    assert "weather" in result.transcript.lower()