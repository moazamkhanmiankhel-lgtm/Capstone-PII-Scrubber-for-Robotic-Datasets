import numpy as np
import pytest
import cv2


from robopii.video_scrubber import (
    _blur_regions,
    scrub_image,
    scrub_video,
)


def test_scrub_image_rejects_missing_input(tmp_path):
    input_path = tmp_path / "missing.jpg"
    output_path = tmp_path / "output.jpg"

    with pytest.raises(FileNotFoundError):
        scrub_image(
            str(input_path),
            str(output_path),
        )


def test_scrub_video_rejects_missing_input(tmp_path):
    input_path = tmp_path / "missing.mp4"
    output_path = tmp_path / "output"

    with pytest.raises(FileNotFoundError):
        scrub_video(
            str(input_path),
            str(output_path),
        )


def test_blur_regions_blurs_detected_area():
    # Create an image containing random visual detail.
    random_generator = np.random.default_rng(42)

    frame = random_generator.integers(
        0,
        256,
        size=(100, 100, 3),
        dtype=np.uint8,
    )

    original_frame = frame.copy()

    blurred_count = _blur_regions(
        frame,
        [(25, 25, 50, 50)],
    )

    assert blurred_count == 1

    # The detected region should have changed.
    assert not np.array_equal(
        frame[25:75, 25:75],
        original_frame[25:75, 25:75],
    )

    # A distant area outside the face should remain unchanged.
    assert np.array_equal(
        frame[0:10, 0:10],
        original_frame[0:10, 0:10],
    )


def test_video_tokens_are_stored_with_primary_record(tmp_path, monkeypatch):
    from robopii import video_pipeline, video_scrubber
    from robopii.storage import configure_storage, get_primary_record, get_protected_vault

    class FakeCapture:
        def __init__(self, path):
            self.frames = 2

        def isOpened(self):
            return True

        def get(self, property_id):
            return 30.0

        def read(self):
            if not self.frames:
                return False, None
            self.frames -= 1
            return True, np.full((50, 50, 3), 125, dtype=np.uint8)

        def release(self):
            pass

    class FakeDetector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeRecognizer:
        def __init__(self):
            self.token = None
            self.loaded_from_vault = False

        def identify(self, face):
            from robopii.face_recognizer import FaceMatch
            return FaceMatch(
                self.token if self.loaded_from_vault else None,
                1.0 if self.loaded_from_vault else 0.0,
                self.loaded_from_vault,
            )

        def create_embedding(self, face):
            return np.array([0.5, 0.5], dtype=np.float32)

        def register(self, token, face):
            self.token = token

        def register_embedding(self, token, embedding):
            assert np.allclose(embedding, [0.5, 0.5])
            self.token = token
            self.loaded_from_vault = True

    input_file = tmp_path / "input.mp4"
    input_file.touch()
    configure_storage(data_dir=tmp_path / "database")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(video_scrubber, "_create_face_detector", lambda mode: FakeDetector())
    monkeypatch.setattr(video_scrubber, "_detect_faces", lambda *args: [(5, 5, 25, 25)])
    monkeypatch.setattr("robopii.face_recognizer.InMemoryFaceRecognizer", FakeRecognizer)
    try:
        result = video_pipeline.process_video_only(str(input_file))
        assert len(result.tokens) == 1
        assert get_primary_record(result.record_id)["tokens"] == result.tokens
        assert get_protected_vault().count_mappings() == 1
        second = video_pipeline.process_video_only(str(input_file))
        assert second.tokens == result.tokens
        assert get_primary_record(second.record_id)["tokens"] == result.tokens
        assert get_protected_vault().count_mappings() == 1
    finally:
        configure_storage()
