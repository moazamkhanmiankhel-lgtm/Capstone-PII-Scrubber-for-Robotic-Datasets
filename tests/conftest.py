"""Fixtures shared by the pipeline, retrieval and config tests.

The integration tests use the real token manager and the real primary store
and vault, in a temporary directory. The model-based components (face
detection, Whisper, spaCy) are replaced with small fakes so these tests run
in about a second on any machine. Tests that exercise the real models are in
``test_pipeline.py`` under ``TestRealComponents`` and are skipped when the
model is not installed.
"""

from pathlib import Path

import pytest

from fakes import FakeImageScrubber, FakeTextScrubber, FakeTranscriber, FakeVideoScrubber
from robopii import storage
from robopii.config import PipelineSettings, RetrievalSettings, Settings, set_config
from robopii.pipeline import PipelineComponents
from robopii.token_manager import SECRET_ENV_VAR


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Point both databases and the vault secret at a temporary folder."""
    monkeypatch.setenv(SECRET_ENV_VAR, "integration-test-secret")
    monkeypatch.delenv("ROBOPII_DATA_DIR", raising=False)
    monkeypatch.delenv("ROBOPII_CONFIG", raising=False)

    data_dir = tmp_path / "data"
    storage.configure_storage(data_dir=data_dir)

    yield data_dir

    storage.configure_storage()


@pytest.fixture
def settings(tmp_path, isolated_storage):
    """Settings that write scrubbed media inside the temporary folder."""
    test_settings = Settings(
        pipeline=PipelineSettings(output_dir=tmp_path / "output"),
        retrieval=RetrievalSettings(
            authorised_actors=("operator",),
            min_reason_length=5,
            max_records=20,
        ),
    )
    set_config(test_settings)

    yield test_settings

    set_config(None)


@pytest.fixture
def fakes():
    """A fresh set of fake components, using the real token manager."""

    class Fakes:
        text = FakeTextScrubber()
        video = FakeVideoScrubber()
        image = FakeImageScrubber()
        audio = FakeTranscriber(
            "Hi, I'm Jane Smith. Email me at jane.smith@example.com."
        )

        def components(self) -> PipelineComponents:
            return PipelineComponents(
                scrub_image=self.image,
                scrub_video=self.video,
                transcribe_audio=self.audio,
                scrub_text=self.text,
            )

    return Fakes()


@pytest.fixture
def media_file(tmp_path):
    """Create a placeholder input file with the given name."""

    def make(name: str) -> Path:
        path = tmp_path / "inputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really media")
        return path

    return make
