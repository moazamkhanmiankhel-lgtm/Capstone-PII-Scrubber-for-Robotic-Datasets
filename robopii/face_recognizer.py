"""Temporary in-memory face recognition using OpenCV SFace."""

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from uuid import uuid4
import cv2
import numpy as np


DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "models"
    / "face_recognition_sface.onnx"
)


@dataclass
class FaceMatch:
    """Result of comparing a face against registered people."""

    token: str | None
    similarity: float
    matched: bool

@dataclass
class PersonResolution:
    """Token assigned after resolving a detected person."""

    token: str
    is_new_person: bool
    similarity: float


class InMemoryFaceRecognizer:
    """Recognise faces without requiring a database."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        similarity_threshold: float = 0.40,
    ):
        self.model_path = Path(model_path)
        self.similarity_threshold = similarity_threshold

        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"SFace model does not exist: {self.model_path}"
            )

        self._recognizer = cv2.FaceRecognizerSF_create(
            str(self.model_path),
            "",
        )

        # Temporary replacement for the future database.
        self._embeddings: dict[str, list[np.ndarray]] = {}

    def create_embedding(
        self,
        face_image: np.ndarray,
    ) -> np.ndarray:
        """Convert a cropped face image into a normalized embedding."""
        if face_image is None or face_image.size == 0:
            raise ValueError("Face image cannot be empty.")

        resized_face = cv2.resize(
            face_image,
            (112, 112),
        )

        embedding = self._recognizer.feature(
            resized_face
        ).flatten()

        magnitude = np.linalg.norm(embedding)

        if magnitude == 0:
            raise ValueError(
                "Could not create a valid face embedding."
            )

        return embedding / magnitude

    def register(
        self,
        token: str,
        face_image: np.ndarray,
    ) -> None:
        """Register a face against an externally supplied token."""
        if not token or not token.strip():
            raise ValueError("Token cannot be empty.")

        self.register_embedding(token, self.create_embedding(face_image))

    def register_embedding(self, token: str, embedding: np.ndarray) -> None:
        """Load a saved face embedding under its existing token."""
        if not token or not token.strip():
            raise ValueError("Token cannot be empty.")
        vector = np.asarray(embedding, dtype=np.float32).flatten()
        magnitude = np.linalg.norm(vector)
        if not vector.size or not np.isfinite(magnitude) or magnitude == 0:
            raise ValueError("Face embedding must be a finite nonzero vector.")
        self._embeddings.setdefault(token, []).append(vector / magnitude)

    def identify(
        self,
        face_image: np.ndarray,
    ) -> FaceMatch:
        """Return the closest registered person token."""
        if not self._embeddings:
            return FaceMatch(
                token=None,
                similarity=0.0,
                matched=False,
            )

        candidate_embedding = self.create_embedding(
            face_image
        )

        best_token = None
        best_similarity = -1.0

        for token, stored_embeddings in self._embeddings.items():
            for stored_embedding in stored_embeddings:
                similarity = float(
                    np.dot(
                        candidate_embedding,
                        stored_embedding,
                    )
                )

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_token = token

        matched = (
            best_token is not None
            and best_similarity
            >= self.similarity_threshold
        )

        return FaceMatch(
            token=best_token if matched else None,
            similarity=best_similarity,
            matched=matched,
        )

    def register_or_identify(
        self,
        token: str,
        face_image: np.ndarray,
    ) -> FaceMatch:
        """
        Identify a face or register it using the supplied token.

        The real token manager will supply the token later.
        """
        match = self.identify(face_image)

        if match.matched:
            return match

        self.register(
            token,
            face_image,
        )

        return FaceMatch(
            token=token,
            similarity=1.0,
            matched=False,
        )

    def registered_tokens(self) -> list[str]:
        """Return the tokens currently held in memory."""
        return list(self._embeddings.keys())

    def clear(self) -> None:
        """Remove all temporary in-memory embeddings."""
        self._embeddings.clear()
        

class InMemoryPersonResolver:
    """Assign and reuse person tokens during one processing session."""

    def __init__(
        self,
        recognizer: InMemoryFaceRecognizer,
        token_factory: Callable[[], str] | None = None,
    ):
        self.recognizer = recognizer
        self.token_factory = (
            token_factory or self._create_temporary_token
        )

    @staticmethod
    def _create_temporary_token() -> str:
        """Create a temporary pseudonymous person token."""
        return f"PERSON_{uuid4().hex[:8].upper()}"

    def resolve(
        self,
        face_image: np.ndarray,
    ) -> PersonResolution:
        """Reuse an existing token or register a new person."""
        match = self.recognizer.identify(face_image)

        if match.matched and match.token is not None:
            return PersonResolution(
                token=match.token,
                is_new_person=False,
                similarity=match.similarity,
            )

        new_token = self.token_factory()

        self.recognizer.register(
            token=new_token,
            face_image=face_image,
        )

        return PersonResolution(
            token=new_token,
            is_new_person=True,
            similarity=match.similarity,
        )
