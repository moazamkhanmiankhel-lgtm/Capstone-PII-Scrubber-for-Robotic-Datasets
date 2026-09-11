import numpy as np
import pytest

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