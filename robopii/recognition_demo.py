import cv2
import mediapipe as mp

from robopii.face_recognizer import (
    InMemoryFaceRecognizer,
    InMemoryPersonResolver,
)
from robopii.video_scrubber import (
    _create_face_detector,
    _detect_faces,
)


def extract_largest_face(image_path):
    """Detect and crop the largest face from an image."""
    image = cv2.imread(image_path)

    if image is None:
        raise ValueError(
            f"Could not read image: {image_path}"
        )

    with _create_face_detector(
        mp.tasks.vision.RunningMode.IMAGE
    ) as detector:
        face_regions = _detect_faces(
            image,
            detector,
        )

    if not face_regions:
        raise ValueError(
            f"No face detected in: {image_path}"
        )

    # Use the largest detected face.
    x, y, width, height = max(
        face_regions,
        key=lambda box: box[2] * box[3],
    )

    frame_height, frame_width = image.shape[:2]

    padding_x = int(width * 0.10)
    padding_y = int(height * 0.10)

    x1 = max(0, x - padding_x)
    y1 = max(0, y - padding_y)
    x2 = min(
        frame_width,
        x + width + padding_x,
    )
    y2 = min(
        frame_height,
        y + height + padding_y,
    )

    return image[y1:y2, x1:x2]


first_face = extract_largest_face(
    "data/original_frames/vid1/frame_000008.jpg"
)

second_face = extract_largest_face(
    "data/original_frames/vid1/frame_000067.jpg"
)

recognizer = InMemoryFaceRecognizer()

resolver = InMemoryPersonResolver(
    recognizer=recognizer,
    token_factory=lambda: "PERSON_TEST_001",
)

first_result = resolver.resolve(first_face)
second_result = resolver.resolve(second_face)

print("First appearance:")
print("Token:", first_result.token)
print("New person:", first_result.is_new_person)
print("Similarity:", first_result.similarity)

print("\nSecond appearance:")
print("Token:", second_result.token)
print("New person:", second_result.is_new_person)
print("Similarity:", second_result.similarity)