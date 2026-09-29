"""Integration and end-to-end tests for the processing pipeline.

The model-based components are faked (see ``fakes.py``); the token
manager, primary store and vault are real, in a temporary directory.
"""

import importlib.util
from pathlib import Path

import pytest

from robopii import storage
from robopii.models import DetectedPII, ProcessingResult, TextScrubResult
from robopii.pipeline import (
    Pipeline,
    UnsupportedInputError,
    audit_primary_store,
    detect_input_type,
    find_leaked_values,
    link_placeholders_to_tokens,
)
from robopii.retrieval import recall_entity, resolve_identity
from robopii.storage import RawPIIError, get_primary_record, get_primary_store
from robopii.token_manager import is_valid_token

from fakes import FakeTextScrubber, FakeTranscriber, FakeVideoScrubber, with_pipeline


TRANSCRIPT = (
    "Hi, I'm Jane Smith from Sydney. Call me on 0412 345 678 "
    "or email jane.smith@example.com."
)
RAW_VALUES = ["Jane Smith", "Sydney", "0412 345 678", "jane.smith@example.com"]


def primary_db_bytes(data_dir: Path) -> bytes:
    storage.close_databases()
    return (data_dir / "primary.db").read_bytes()


# Helpers ------------------------------------------------------------------


class TestInputTypes:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("photo.JPG", "image"),
            ("frame.png", "image"),
            ("clip.mp4", "video"),
            ("clip.MOV", "video"),
            ("speech.wav", "audio"),
            ("speech.mp3", "audio"),
            ("notes.txt", "transcript"),
        ],
    )
    def test_detects_supported_types(self, name, expected):
        assert detect_input_type(name) == expected

    @pytest.mark.parametrize("name", ["data.csv", "archive.zip", "no_extension"])
    def test_rejects_unsupported_types(self, name):
        with pytest.raises(UnsupportedInputError):
            detect_input_type(name)


class TestPlaceholderLinking:
    def test_replaces_placeholders_with_tokens(self):
        text = "[PERSON_1] met [PERSON_2]."
        linked = link_placeholders_to_tokens(
            text,
            {"[PERSON_1]": "PERSON_AAAAAAAAAAAA", "[PERSON_2]": "PERSON_BBBBBBBBBBBB"},
        )

        assert linked == "[PERSON_AAAAAAAAAAAA] met [PERSON_BBBBBBBBBBBB]."

    def test_person_1_does_not_touch_person_10(self):
        text = "[PERSON_1] and [PERSON_10]"
        linked = link_placeholders_to_tokens(
            text,
            {"[PERSON_1]": "PERSON_111111111111", "[PERSON_10]": "PERSON_101010101010"},
        )

        assert linked == "[PERSON_111111111111] and [PERSON_101010101010]"

    def test_no_placeholders_leaves_text_unchanged(self):
        assert link_placeholders_to_tokens("hello", {}) == "hello"


class TestLeakDetection:
    def test_finds_value_left_in_text(self):
        entity = DetectedPII("PERSON", "Jane Smith", "[PERSON_1]")

        assert find_leaked_values("[PERSON_1] said jane smith", [entity]) == [entity]

    def test_ignores_value_inside_another_word(self):
        entity = DetectedPII("PERSON", "Jan", "[PERSON_1]")

        assert find_leaked_values("It was January.", [entity]) == []

    def test_short_values_are_case_sensitive(self):
        entity = DetectedPII("LOCATION", "US", "[LOCATION_1]")

        assert find_leaked_values("Can you help us?", [entity]) == []
        assert find_leaked_values("Back in the US now", [entity]) == [entity]


# Transcript route ---------------------------------------------------------


class TestTranscriptPipeline:
    def test_stores_scrubbed_transcript_with_valid_tokens(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        assert isinstance(result, ProcessingResult)
        assert len(result.tokens) == 4
        assert all(is_valid_token(token) for token in result.tokens)

        for value in RAW_VALUES:
            assert value not in result.scrubbed_transcript

        stored = get_primary_record(result.record_id)
        assert stored["scrubbed_transcript"] == result.scrubbed_transcript
        assert stored["tokens"] == result.tokens
        assert stored["source_label"] == "transcript"
        assert stored["scrubbed_media_path"] is None

    def test_transcript_names_tokens_instead_of_placeholders(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        for token in result.tokens:
            assert f"[{token}]" in result.scrubbed_transcript

        assert "[PERSON_1]" not in result.scrubbed_transcript

    def test_placeholders_kept_when_linking_is_off(self, settings, fakes):
        settings = with_pipeline(settings, link_tokens_in_transcript=False)
        result = Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        assert "[PERSON_1]" in result.scrubbed_transcript
        assert "[EMAIL_1]" in result.scrubbed_transcript

    def test_recurring_person_gets_the_same_token(self, settings, fakes):
        pipeline = Pipeline(settings, fakes.components())

        first = pipeline.process_transcript("Hello, I'm Jane Smith.")
        second = pipeline.process_transcript("Jane Smith is back, with Bob Lee.")

        jane_token = first.tokens[0]

        assert second.tokens[0] == jane_token
        assert len(set(second.tokens)) == 2
        assert get_primary_store().count_records() == 2

    def test_repeated_mention_in_one_transcript_gives_one_token(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript(
            "Jane Smith said hello. Later Jane Smith left."
        )

        assert len(result.tokens) == 1
        assert result.scrubbed_transcript.count(f"[{result.tokens[0]}]") == 2

    def test_text_without_pii_is_stored_without_tokens(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript(
            "The robot moved to the kitchen."
        )

        assert result.tokens == []
        assert result.scrubbed_transcript == "The robot moved to the kitchen."

    def test_metadata_records_counts_and_timings(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)
        metadata = get_primary_record(result.record_id)["metadata"]

        assert metadata["input_type"] == "transcript"
        assert metadata["pii_counts"] == {"PERSON": 1, "LOCATION": 1, "PHONE": 1, "EMAIL": 1}
        assert {"text_scrub", "tokenisation", "total_before_storage"} <= set(
            metadata["timings_seconds"]
        )

    def test_transcript_file(self, settings, fakes, tmp_path):
        path = tmp_path / "chat.txt"
        path.write_text(TRANSCRIPT, encoding="utf-8")

        result = Pipeline(settings, fakes.components()).process_file(path)

        assert len(result.tokens) == 4

    def test_rejects_non_string_transcript(self, settings, fakes):
        with pytest.raises(TypeError):
            Pipeline(settings, fakes.components()).process_transcript(None)


class TestRawPIIIsRefused:
    def test_leftover_name_stops_the_record(self, settings, fakes):
        """A scrubber that misses a lower-case repeat must not be stored."""

        def leaky_scrubber(text):
            return TextScrubResult(
                scrubbed_text=text.replace("Jane Smith", "[PERSON_1]"),
                detected_pii=[DetectedPII("PERSON", "Jane Smith", "[PERSON_1]")],
            )

        components = fakes.components()
        components.scrub_text = leaky_scrubber

        with pytest.raises(RawPIIError) as error:
            Pipeline(settings, components).process_transcript(
                "Jane Smith arrived. Everyone greeted jane smith."
            )

        assert "Jane" not in str(error.value)
        assert get_primary_store().count_records() == 0

    def test_undetected_phone_number_is_blocked_by_storage(self, settings, fakes):
        """The storage guard is the second line of defence."""
        components = fakes.components()
        components.scrub_text = lambda text: TextScrubResult(text, [])

        with pytest.raises(RawPIIError):
            Pipeline(settings, components).process_transcript("Call 0412 345 678")

        assert get_primary_store().count_records() == 0

    def test_primary_database_file_contains_no_raw_values(
        self, settings, fakes, isolated_storage
    ):
        Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        raw = primary_db_bytes(isolated_storage).lower()

        for value in RAW_VALUES:
            assert value.lower().encode() not in raw

    def test_vault_holds_values_encrypted(self, settings, fakes, isolated_storage):
        result = Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        storage.close_databases()
        vault_bytes = (isolated_storage / "vault.db").read_bytes().lower()

        for value in RAW_VALUES:
            assert value.lower().encode() not in vault_bytes

        mapping = resolve_identity(result.tokens[0], actor="operator", reason="unit test")
        assert mapping.original_value == "Jane Smith"


# Video route --------------------------------------------------------------


class TestVideoPipeline:
    def test_video_with_audio(self, settings, fakes, media_file):
        clip = media_file("jane_smith_0412345678.mp4")
        result = Pipeline(settings, fakes.components()).process_video(clip)

        stored = get_primary_record(result.record_id)

        assert result.scrubbed_media_path == str(settings.pipeline.output_dir / result.record_id)
        assert Path(result.scrubbed_media_path, "frame_000001.jpg").is_file()
        assert "jane" not in result.scrubbed_media_path.lower()
        assert len(result.tokens) == 2
        assert stored["metadata"]["faces_detected"] == 2
        assert stored["metadata"]["audio_status"] == "transcribed"
        assert {"visual", "transcription"} <= set(stored["metadata"]["timings_seconds"])

    def test_original_frames_are_deleted(self, settings, fakes, media_file):
        Pipeline(settings, fakes.components()).process_video(media_file("clip.mp4"))

        assert fakes.video.original_dirs
        assert not fakes.video.original_dirs[0].exists()

    def test_original_frames_kept_only_when_configured(
        self, settings, fakes, media_file, isolated_storage
    ):
        settings = with_pipeline(settings, keep_original_frames=True)
        result = Pipeline(settings, fakes.components()).process_video(media_file("clip.mp4"))

        kept = isolated_storage / "original_frames" / result.record_id
        assert (kept / "frame_000001.jpg").is_file()

    def test_video_without_audio_is_stored_without_transcript(
        self, settings, fakes, media_file
    ):
        fakes.audio = FakeTranscriber(no_audio=True)
        result = Pipeline(settings, fakes.components()).process_video(media_file("clip.mp4"))

        stored = get_primary_record(result.record_id)

        assert result.scrubbed_transcript is None
        assert result.tokens == []
        assert stored["metadata"]["audio_status"] == "no_audio"

    def test_unavailable_transcriber_still_stores_frames(
        self, settings, fakes, media_file
    ):
        components = fakes.components()

        def stub(input_path):
            raise NotImplementedError

        components.transcribe_audio = stub
        result = Pipeline(settings, components).process_video(media_file("clip.mp4"))

        stored = get_primary_record(result.record_id)
        assert stored["metadata"]["audio_status"] == "transcriber_unavailable"
        assert result.scrubbed_transcript is None

    def test_transcription_can_be_switched_off(self, settings, fakes, media_file):
        settings = with_pipeline(settings, transcribe_video_audio=False)
        result = Pipeline(settings, fakes.components()).process_video(media_file("clip.mp4"))

        assert fakes.audio.calls == []
        assert get_primary_record(result.record_id)["metadata"]["audio_status"] == "skipped"

    def test_failure_cleans_up_frames_and_stores_nothing(
        self, settings, fakes, media_file
    ):
        fakes.video = FakeVideoScrubber(fail=True)

        with pytest.raises(RuntimeError):
            Pipeline(settings, fakes.components()).process_video(media_file("clip.mp4"))

        assert not any(settings.pipeline.output_dir.glob("*"))
        assert not fakes.video.original_dirs[0].exists()
        assert get_primary_store().count_records() == 0

    def test_leak_in_transcript_removes_scrubbed_frames(self, settings, fakes, media_file):
        components = fakes.components()
        components.scrub_text = lambda text: TextScrubResult(text, [])
        fakes.audio.transcript = "my number is 0412 345 678"

        with pytest.raises(RawPIIError):
            Pipeline(settings, components).process_video(media_file("clip.mp4"))

        assert not any(settings.pipeline.output_dir.glob("*"))

    def test_missing_file(self, settings, fakes, tmp_path):
        with pytest.raises(FileNotFoundError):
            Pipeline(settings, fakes.components()).process_video(tmp_path / "nope.mp4")


class TestImageAndAudioPipeline:
    def test_image(self, settings, fakes, media_file):
        result = Pipeline(settings, fakes.components()).process_file(
            media_file("Jane Smith portrait.jpg")
        )

        stored = get_primary_record(result.record_id)

        assert Path(result.scrubbed_media_path).is_file()
        assert "jane" not in result.scrubbed_media_path.lower()
        assert result.scrubbed_transcript is None
        assert stored["metadata"]["faces_detected"] == 1
        assert stored["metadata"]["audio_status"] == "not_applicable"

    def test_audio(self, settings, fakes, media_file):
        result = Pipeline(settings, fakes.components()).process_file(media_file("call.wav"))

        assert result.scrubbed_media_path is None
        assert len(result.tokens) == 2
        assert "jane.smith@example.com" not in result.scrubbed_transcript


# End to end ---------------------------------------------------------------


class TestEndToEnd:
    def test_meet_store_recall_reveal_forget(self, settings, fakes, media_file):
        """The full story the demo tells."""
        from robopii.retrieval import build_context, forget_identity

        pipeline = Pipeline(settings, fakes.components())

        # Day 1: the robot meets Jane on video.
        first = pipeline.process_video(media_file("day1.mp4"))
        jane = first.tokens[0]

        # Day 2: Jane mentions her name again in conversation.
        pipeline.process_transcript("Jane Smith asked about the charging dock.")

        # The robot recognises the name without looking at the vault plaintext.
        token, records = recall_entity("PERSON", "jane smith")
        assert token == jane
        assert len(records) == 2
        assert "charging dock" in records[0]["scrubbed_transcript"]

        summary = build_context(jane)
        assert summary.interaction_count == 2

        # An operator can see who it is, and that is logged.
        mapping = resolve_identity(jane, actor="operator", reason="returning visitor")
        assert mapping.original_value == "Jane Smith"

        # Jane withdraws consent.
        assert forget_identity(jane, actor="operator", reason="consent withdrawn")
        assert resolve_identity(jane, actor="operator", reason="check again") is None
        assert recall_entity("PERSON", "Jane Smith") == (None, [])

        # Her records remain, de-identified.
        assert len(get_primary_store().list_records()) == 2

        # Nothing raw in the primary store.
        assert audit_primary_store().passed


class TestAudit:
    def test_clean_store_passes(self, settings, fakes):
        Pipeline(settings, fakes.components()).process_transcript(TRANSCRIPT)

        report = audit_primary_store()

        assert report.passed
        assert report.records_checked == 1
        assert report.identities_checked == 4

    def test_detects_a_raw_name_the_pattern_guard_cannot_see(self, settings, fakes):
        Pipeline(settings, fakes.components()).process_transcript("I'm Jane Smith.")

        # Simulate a bug elsewhere writing a raw name into a record.
        storage.save_primary_record({"scrubbed_transcript": "Jane Smith waved."})

        report = audit_primary_store()

        assert not report.passed
        assert any("contains the raw value" in problem for problem in report.problems)
        assert all("Jane" not in problem for problem in report.problems)

    def test_audit_is_logged_in_the_vault(self, settings, fakes):
        result = Pipeline(settings, fakes.components()).process_transcript("I'm Jane Smith.")
        audit_primary_store()

        history = storage.get_protected_vault().access_history(token=result.tokens[0])

        assert history[0]["actor"] == "audit"


# Real components ----------------------------------------------------------


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _spacy_model_available() -> bool:
    if not _has_module("spacy"):
        return False

    import spacy.util

    return spacy.util.is_package("en_core_web_md")


class TestRealComponents:
    """Run the pipeline with the teammates' real components where installed."""

    @pytest.mark.skipif(
        not (_has_module("cv2") and _has_module("mediapipe")),
        reason="OpenCV and MediaPipe are required",
    )
    def test_real_video_scrubber(self, settings, fakes, tmp_path):
        import cv2
        import numpy as np

        clip = tmp_path / "synthetic.avi"
        writer = cv2.VideoWriter(
            str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120)
        )

        for index in range(5):
            frame = np.full((120, 160, 3), 40 * index, dtype=np.uint8)
            writer.write(frame)

        writer.release()

        components = fakes.components()
        from robopii.video_scrubber import scrub_video

        components.scrub_video = scrub_video
        components.transcribe_audio = FakeTranscriber(no_audio=True)

        result = Pipeline(settings, components).process_video(clip)

        frames = sorted(Path(result.scrubbed_media_path).glob("frame_*.jpg"))
        assert len(frames) == 5
        assert get_primary_record(result.record_id)["metadata"]["faces_detected"] == 0

    @pytest.mark.skipif(
        not _spacy_model_available(),
        reason="spaCy and en_core_web_md are required",
    )
    def test_real_text_scrubber(self, settings, fakes, isolated_storage):
        from robopii.text_scrubber import scrub_text

        components = fakes.components()
        components.scrub_text = scrub_text

        result = Pipeline(settings, components).process_transcript(TRANSCRIPT)

        assert result.tokens
        assert "jane.smith@example.com" not in result.scrubbed_transcript
        assert audit_primary_store().passed
