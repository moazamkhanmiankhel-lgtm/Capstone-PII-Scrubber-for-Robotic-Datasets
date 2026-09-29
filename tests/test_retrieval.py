"""Tests for controlled context retrieval."""

from unittest.mock import patch

import pytest

from robopii import storage
from robopii.pipeline import Pipeline
from robopii.retrieval import (
    AccessDeniedError,
    build_context,
    forget_identity,
    is_authorised,
    recall_entity,
    resolve_identity,
    retrieve_context,
)


TOKEN = "PERSON_A1B2C3D4E5F6"


# Unit tests (storage mocked) ---------------------------------------------


def test_retrieve_context_returns_matching_records(settings):
    """Retrieval should return records associated with a token."""
    expected_records = [
        {
            "record_id": "record-001",
            "scrubbed_transcript": "Hello [PERSON_A1B2C3D4E5F6].",
            "tokens": [TOKEN],
        }
    ]

    with patch(
        "robopii.retrieval.find_records_by_token",
        return_value=expected_records,
    ):
        result = retrieve_context(TOKEN)

    assert result == expected_records


@pytest.mark.parametrize("token", ["", "   "])
def test_retrieve_context_rejects_empty_token(settings, token):
    with pytest.raises(ValueError):
        retrieve_context(token)


@pytest.mark.parametrize("token", ["Jane Smith", "PERSON_001", "person_a1b2c3d4e5f6"])
def test_retrieve_context_rejects_values_that_are_not_tokens(settings, token):
    """A raw name must never be usable as a lookup key."""
    with pytest.raises(ValueError):
        retrieve_context(token)


def test_retrieve_context_strips_whitespace(settings):
    with patch("robopii.retrieval.find_records_by_token", return_value=[]) as find:
        retrieve_context(f"  {TOKEN}\n")

    find.assert_called_once_with(TOKEN)


def test_retrieve_context_applies_limit(settings):
    records = [{"record_id": str(index)} for index in range(30)]

    with patch("robopii.retrieval.find_records_by_token", return_value=records):
        assert len(retrieve_context(TOKEN)) == settings.retrieval.max_records
        assert len(retrieve_context(TOKEN, limit=3)) == 3

    with pytest.raises(ValueError):
        retrieve_context(TOKEN, limit=0)


class TestAuthorisation:
    def test_listed_actor_with_reason_is_allowed(self, settings):
        assert is_authorised("operator", "returning visitor")[0]

    @pytest.mark.parametrize(
        "actor, reason",
        [
            ("intruder", "returning visitor"),
            ("", "returning visitor"),
            ("operator", ""),
            ("operator", "hi"),
        ],
    )
    def test_refused(self, settings, actor, reason):
        allowed, explanation = is_authorised(actor, reason)

        assert not allowed
        assert explanation


# Integration tests (real storage) ----------------------------------------


@pytest.fixture
def jane(settings, fakes):
    """Store two interactions with Jane and return her token."""
    pipeline = Pipeline(settings, fakes.components())
    first = pipeline.process_transcript("Hello, I'm Jane Smith.")
    pipeline.process_transcript("Jane Smith came back with Bob Lee.")

    return first.tokens[0]


class TestContext:
    def test_newest_record_first(self, jane):
        records = retrieve_context(jane)

        assert len(records) == 2
        assert "came back" in records[0]["scrubbed_transcript"]

    def test_unknown_token_returns_nothing(self, settings):
        storage.initialise_databases()

        assert retrieve_context(TOKEN) == []

    def test_build_context_summary(self, jane):
        summary = build_context(jane)

        assert summary.token == jane
        assert summary.interaction_count == 2
        assert summary.first_seen <= summary.last_seen
        assert len(summary.related_tokens) == 1
        assert summary.related_tokens[0].startswith("PERSON_")

    def test_context_does_not_touch_the_vault_log(self, jane):
        build_context(jane)
        retrieve_context(jane)

        assert storage.get_protected_vault().access_history() == []


class TestRecall:
    def test_known_value_finds_token_and_history(self, jane):
        token, records = recall_entity("PERSON", "  JANE   smith ")

        assert token == jane
        assert len(records) == 2

    def test_unknown_value(self, jane):
        assert recall_entity("PERSON", "Someone Else") == (None, [])

    def test_recall_does_not_create_a_token(self, jane):
        before = storage.get_protected_vault().count_mappings()
        recall_entity("PERSON", "Someone Else")

        assert storage.get_protected_vault().count_mappings() == before

    def test_empty_value(self, settings):
        with pytest.raises(ValueError):
            recall_entity("PERSON", " ")


class TestIdentityResolution:
    def test_authorised_lookup_is_returned_and_logged(self, jane):
        mapping = resolve_identity(jane, actor="operator", reason="returning visitor")

        assert mapping.original_value == "Jane Smith"

        entry = storage.get_protected_vault().access_history(token=jane)[0]
        assert entry["action"] == "lookup"
        assert entry["actor"] == "operator"
        assert entry["reason"] == "returning visitor"

    def test_unauthorised_lookup_is_refused_and_logged(self, jane):
        with pytest.raises(AccessDeniedError):
            resolve_identity(jane, actor="intruder", reason="curious about it")

        entry = storage.get_protected_vault().access_history(token=jane)[0]
        assert entry["action"] == "denied"
        assert entry["actor"] == "intruder"

    def test_lookup_without_reason_is_refused(self, jane):
        with pytest.raises(AccessDeniedError):
            resolve_identity(jane, actor="operator", reason="")

    def test_unknown_token_returns_none(self, jane):
        assert resolve_identity(TOKEN, actor="operator", reason="checking") is None

    def test_forget_removes_identity_but_keeps_records(self, jane):
        assert forget_identity(jane, actor="operator", reason="consent withdrawn")

        assert resolve_identity(jane, actor="operator", reason="checking") is None
        assert len(retrieve_context(jane)) == 2

        actions = [
            entry["action"]
            for entry in storage.get_protected_vault().access_history(token=jane)
        ]
        assert "delete" in actions

    def test_forget_requires_authorisation(self, jane):
        with pytest.raises(AccessDeniedError):
            forget_identity(jane, actor="intruder", reason="delete everything")

        assert resolve_identity(jane, actor="operator", reason="still here") is not None
