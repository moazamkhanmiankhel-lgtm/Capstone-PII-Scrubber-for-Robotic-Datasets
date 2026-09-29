"""Detect and blur faces in images and videos."""

from pathlib import Path
from time import perf_counter
import base64
import numpy as np

import cv2
import mediapipe as mp

from robopii.models import DetectedPII, VisualScrubResult


MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "models"
    / "blaze_face_short_range.tflite"
)


def _box_overlap(a, b) -> float:
    """Return intersection over union for two face boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0, min(ay + ah, by + bh) - max(ay, by)
    )
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def _create_face_detector(running_mode):
    """Create a MediaPipe face detector."""
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(
            f"MediaPipe model does not exist: {MODEL_PATH}"
        )

    options = mp.tasks.vision.FaceDetectorOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=str(MODEL_PATH)
        ),
        running_mode=running_mode,
        min_detection_confidence=0.6,
        min_suppression_threshold=0.3,
    )

    return mp.tasks.vision.FaceDetector.create_from_options(
        options
    )


def _detect_faces(
    frame,
    detector,
    timestamp_ms: int | None = None,
):
    """Detect faces and return OpenCV-style bounding boxes."""
    rgb_frame = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB,
    )

    mediapipe_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=rgb_frame,
    )

    if timestamp_ms is None:
        result = detector.detect(mediapipe_image)
    else:
        result = detector.detect_for_video(
            mediapipe_image,
            timestamp_ms,
        )

    face_regions = []

    for detection in result.detections:
        box = detection.bounding_box

        face_regions.append(
            (
                box.origin_x,
                box.origin_y,
                box.width,
                box.height,
            )
        )

    return face_regions


def _blur_regions(frame, regions) -> int:
    """Apply a strong blur to detected face regions."""
    frame_height, frame_width = frame.shape[:2]
    blurred_count = 0

    for x, y, width, height in regions:
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)

        # Keep the blur close to the face.
        horizontal_padding = int(width * 0.05)
        top_padding = int(height * 0.25)
        bottom_padding = int(height * 0.05)

        x1 = max(0, x - horizontal_padding)
        y1 = max(0, y - top_padding)
        x2 = min(
            frame_width,
            x + width + horizontal_padding,
        )
        y2 = min(
            frame_height,
            y + height + bottom_padding,
        )

        if x2 <= x1 or y2 <= y1:
            continue

        face_region = frame[y1:y2, x1:x2]

        region_width = x2 - x1
        region_height = y2 - y1

        blur_strength = max(
            15,
            int(
                max(region_width, region_height)
                * 0.20
            ),
        )

        blurred_region = cv2.GaussianBlur(
            face_region,
            (0, 0),
            sigmaX=blur_strength,
            sigmaY=blur_strength,
        )

        blurred_region = cv2.GaussianBlur(
            blurred_region,
            (0, 0),
            sigmaX=blur_strength,
            sigmaY=blur_strength,
        )

        frame[y1:y2, x1:x2] = blurred_region
        blurred_count += 1

    return blurred_count


def scrub_image(
    input_path: str,
    output_path: str,
) -> VisualScrubResult:
    """Detect and blur faces in one image."""
    started_at = perf_counter()

    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Input image does not exist: {input_file}"
        )

    image = cv2.imread(str(input_file))

    if image is None:
        raise ValueError(
            f"OpenCV could not read the image: {input_file}"
        )

    with _create_face_detector(
        mp.tasks.vision.RunningMode.IMAGE
    ) as detector:
        faces = _detect_faces(
            image,
            detector,
        )

    faces_detected = _blur_regions(
        image,
        faces,
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not cv2.imwrite(str(output_file), image):
        raise OSError(
            f"Could not write scrubbed image: {output_file}"
        )

    return VisualScrubResult(
        output_path=str(output_file),
        faces_detected=faces_detected,
        processing_time_seconds=(
            perf_counter() - started_at
        ),
    )


def scrub_video(
    input_path: str,
    output_path: str,
    original_output_dir: str | None = None,
    tokenize_faces: bool = False,
) -> VisualScrubResult:
    """Extract original and scrubbed image frames from a video."""
    started_at = perf_counter()

    input_file = Path(input_path)
    scrubbed_dir = Path(output_path)

    if original_output_dir is None:
        original_dir = (
            Path("data")
            / "original_frames"
            / input_file.stem
        )
    else:
        original_dir = Path(original_output_dir)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Input video does not exist: {input_file}"
        )

    capture = cv2.VideoCapture(str(input_file))

    if not capture.isOpened():
        capture.release()
        raise ValueError(
            f"OpenCV could not open the video: {input_file}"
        )

    fps = capture.get(cv2.CAP_PROP_FPS)

    if fps <= 0:
        fps = 30.0

    original_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    scrubbed_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    frame_number = 0
    faces_detected = 0
    tokens: set[str] = set()
    recent_faces: list[tuple[tuple[int, int, int, int], str, int]] = []
    recognizer = None
    if tokenize_faces:
        from robopii.face_recognizer import InMemoryFaceRecognizer
        from robopii.storage import get_protected_vault

        recognizer = InMemoryFaceRecognizer()
        for mapping in get_protected_vault().list_face_mappings():
            vector = np.frombuffer(
                base64.b64decode(mapping.original_value, validate=True),
                dtype="<f4",
            )
            recognizer.register_embedding(mapping.token, vector)

    try:
        with _create_face_detector(
            mp.tasks.vision.RunningMode.VIDEO
        ) as detector:
            while True:
                success, frame = capture.read()

                if not success:
                    break

                frame_number += 1

                filename = (
                    f"frame_{frame_number:06d}.jpg"
                )

                original_path = (
                    original_dir / filename
                )

                scrubbed_path = (
                    scrubbed_dir / filename
                )

                if not cv2.imwrite(
                    str(original_path),
                    frame,
                ):
                    raise OSError(
                        "Could not save original frame: "
                        f"{original_path}"
                    )

                timestamp_ms = int(
                    frame_number * 1000 / fps
                )

                face_regions = _detect_faces(
                    frame,
                    detector,
                    timestamp_ms,
                )

                if recognizer is not None:
                    from robopii.token_manager import resolve_or_create_token

                    frame_height, frame_width = frame.shape[:2]
                    recent_faces = [
                        track for track in recent_faces
                        if frame_number - track[2] <= 10
                    ]
                    used_tracks: set[int] = set()
                    for x, y, width, height in face_regions:
                        x1, y1 = max(0, int(x)), max(0, int(y))
                        x2 = min(frame_width, int(x + width))
                        y2 = min(frame_height, int(y + height))
                        if x2 <= x1 or y2 <= y1:
                            continue
                        face = frame[y1:y2, x1:x2]
                        box = (x1, y1, x2 - x1, y2 - y1)
                        nearest = max(
                            (i for i in range(len(recent_faces)) if i not in used_tracks),
                            key=lambda i: _box_overlap(box, recent_faces[i][0]),
                            default=None,
                        )
                        if nearest is not None and _box_overlap(
                            box, recent_faces[nearest][0]
                        ) >= 0.3:
                            token = recent_faces[nearest][1]
                            recent_faces[nearest] = (box, token, frame_number)
                            used_tracks.add(nearest)
                        else:
                            match = recognizer.identify(face)
                            if match.matched and match.token is not None:
                                token = match.token
                            else:
                                # Store the template only in the encrypted vault.
                                embedding = recognizer.create_embedding(face)
                                value = base64.b64encode(
                                    embedding.astype("<f4").tobytes()
                                ).decode("ascii")
                                mapping = resolve_or_create_token(
                                    DetectedPII("FACE", value, "[FACE]")
                                )
                                token = mapping.token
                                recognizer.register(token, face)
                            recent_faces.append((box, token, frame_number))
                            used_tracks.add(len(recent_faces) - 1)
                        tokens.add(token)

                scrubbed_frame = frame.copy()

                faces_detected += _blur_regions(
                    scrubbed_frame,
                    face_regions,
                )

                if not cv2.imwrite(
                    str(scrubbed_path),
                    scrubbed_frame,
                ):
                    raise OSError(
                        "Could not save scrubbed frame: "
                        f"{scrubbed_path}"
                    )

    finally:
        capture.release()

    return VisualScrubResult(
        output_path=str(scrubbed_dir),
        faces_detected=faces_detected,
        processing_time_seconds=(
            perf_counter() - started_at
        ),
        tokens=sorted(tokens),
    )
