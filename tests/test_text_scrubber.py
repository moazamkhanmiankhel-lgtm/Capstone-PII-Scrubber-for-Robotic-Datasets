import pytest

from robopii.models import TextScrubResult
from robopii.text_scrubber import scrub_text


def _values(result, pii_type):
    return [
        item.original_value
        for item in result.detected_pii
        if item.pii_type == pii_type
    ]


def test_returns_text_scrub_result():
    result = scrub_text("nothing sensitive here")

    assert isinstance(result, TextScrubResult)
    assert result.scrubbed_text == "nothing sensitive here"
    assert result.detected_pii == []


def test_empty_text_is_returned_unchanged():
    assert scrub_text("").scrubbed_text == ""


def test_non_string_raises():
    with pytest.raises(TypeError):
        scrub_text(None)


def test_detects_typed_email():
    result = scrub_text("write to jane.smith@example.com today")

    assert "jane.smith@example.com" not in result.scrubbed_text
    assert _values(result, "EMAIL") == ["jane.smith@example.com"]


def test_detects_spoken_email():
    result = scrub_text("my email is jane dot smith at example dot com")

    assert "example" not in result.scrubbed_text
    assert len(_values(result, "EMAIL")) == 1


def test_detects_phone_with_spaces():
    result = scrub_text("call me on 0412 345 678 tomorrow")

    assert "0412" not in result.scrubbed_text
    assert _values(result, "PHONE") == ["0412 345 678"]


def test_detects_phone_regrouped_by_transcription():
    result = scrub_text("call me on 041-2345-678 tomorrow")

    assert "2345" not in result.scrubbed_text
    assert len(_values(result, "PHONE")) == 1


def test_detects_person_name():
    result = scrub_text("Jane Smith arrived at nine")

    assert "Jane Smith" not in result.scrubbed_text
    assert _values(result, "PERSON") == ["Jane Smith"]


def test_repeated_value_reuses_one_placeholder():
    result = scrub_text("Jane Smith called. Jane Smith left a message.")

    assert result.scrubbed_text.count("[PERSON_1]") == 2
    assert len(_values(result, "PERSON")) == 1


def test_different_people_get_different_placeholders():
    result = scrub_text("Jane Smith rang Aiden Channell")

    placeholders = {item.placeholder for item in result.detected_pii}

    assert len(placeholders) == 2


def test_scrubbed_text_contains_no_original_values():
    text = "Jane Smith on 0412 345 678 or jane.smith@example.com"
    result = scrub_text(text)

    for item in result.detected_pii:
        assert item.original_value not in result.scrubbed_text


def test_transcribed_email_without_at_sign_is_detected():
    """The at sign rarely survives transcription, so the spoken form
    with literal dots must still be caught."""
    result = scrub_text("email me at jane.smith at example.com")

    assert "jane.smith" not in result.scrubbed_text
    assert "example.com" not in result.scrubbed_text
    assert result.detected_pii[0].pii_type == "EMAIL"


def test_bare_domain_is_redacted_as_a_known_false_positive():
    """Domain suffix matching over-redacts ordinary web addresses.
    This is deliberate: over-redaction is the safer failure."""
    result = scrub_text("Check out westfield.com for the sale")

    assert "westfield.com" not in result.scrubbed_text


def test_at_in_ordinary_speech_is_not_treated_as_an_email():
    result = scrub_text("Meet me at the shops")

    assert result.scrubbed_text == "Meet me at the shops"