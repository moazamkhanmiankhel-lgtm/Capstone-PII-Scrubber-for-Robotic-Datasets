import numpy as np
import pytest

from robopii.face_recognizer import (
    FaceMatch,
    InMemoryFaceRecognizer,
)


@pytest.fixture
def recognizer_without_model():
    recognizer = object.__new__(InMemoryFaceRecognizer)
    recognizer.similarity_threshold = 0.40
    recognizer._embeddings = {}
    return recognizer


def test_identify_returns_no_match_when_empty(
    recognizer_without_model,
):
    recognizer = recognizer_without_model

    recognizer.create_embedding = lambda _: np.array(
        [1.0, 0.0]
    )

    result = recognizer.identify(
        np.zeros((10, 10, 3), dtype=np.uint8)
    )

    assert isinstance(result, FaceMatch)
    assert result.matched is False
    assert result.token is None


def test_identify_returns_matching_token(
    recognizer_without_model,
):
    recognizer = recognizer_without_model

    recognizer._embeddings = {
        "PERSON_001": [np.array([1.0, 0.0])],
        "PERSON_002": [np.array([0.0, 1.0])],
    }

    recognizer.create_embedding = lambda _: np.array(
        [0.99, 0.01]
    )

    result = recognizer.identify(
        np.zeros((10, 10, 3), dtype=np.uint8)
    )

    assert result.matched is True
    assert result.token == "PERSON_001"
    assert result.similarity > 0.9


def test_identify_rejects_different_face(
    recognizer_without_model,
):
    recognizer = recognizer_without_model

    recognizer._embeddings = {
        "PERSON_001": [np.array([1.0, 0.0])]
    }

    recognizer.create_embedding = lambda _: np.array(
        [0.0, 1.0]
    )

    result = recognizer.identify(
        np.zeros((10, 10, 3), dtype=np.uint8)
    )

    assert result.matched is False
    assert result.token is None


def test_register_rejects_empty_token(
    recognizer_without_model,
):
    with pytest.raises(ValueError):
        recognizer_without_model.register(
            "",
            np.zeros((10, 10, 3), dtype=np.uint8),
        )


def test_person_resolver_reuses_existing_token(
    recognizer_without_model,
):
    from robopii.face_recognizer import (
        InMemoryPersonResolver,
    )

    recognizer = recognizer_without_model

    recognizer.create_embedding = lambda _: np.array(
        [1.0, 0.0]
    )

    resolver = InMemoryPersonResolver(
        recognizer=recognizer,
        token_factory=lambda: "PERSON_TEST_001",
    )

    face = np.zeros(
        (10, 10, 3),
        dtype=np.uint8,
    )

    first_result = resolver.resolve(face)
    second_result = resolver.resolve(face)

    assert first_result.token == "PERSON_TEST_001"
    assert first_result.is_new_person is True

    assert second_result.token == "PERSON_TEST_001"
    assert second_result.is_new_person is False
    assert second_result.similarity == pytest.approx(1.0)