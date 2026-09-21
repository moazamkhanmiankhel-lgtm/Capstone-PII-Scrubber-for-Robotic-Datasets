"""Tests for controlled context retrieval"""

from unittest.mock import patch

import pytest

from robopii.retrieval import retrieve_context

def test_retreive_context_returns_matching_records():
    """retrieval should return records accociated with a token"""

    expected_records = [
        {
            "record_id": "record-001",
            "scrubbed_transcript": "Hello [PERSON].",
            "tokens": ["PERSON_001"],
        }
    ]

    with patch(
        "robopii.retrieval.find_records_by_token",
        return_value=expected_records,
    ):
        result = retrieve_context("PERSON_001")

    assert result == expected_records

def test_retreive_context_rejects_empty_token(): 

    with pytest.raises(ValueError):
        retrieve_context("")