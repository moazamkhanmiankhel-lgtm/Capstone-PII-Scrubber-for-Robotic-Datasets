import pytest

from robopii.models import DetectedPII, TokenMapping
from robopii.storage import ProtectedVault
from robopii.token_manager import (
    SECRET_ENV_VAR,
    TokenManager,
    create_token,
    encryption_key,
    is_valid_token,
    lookup_hash,
    normalise_value,
    resolve_or_create_token,
    secret_key,
    token_prefix,
)


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    """Use a predictable vault secret in every test."""
    monkeypatch.setenv(SECRET_ENV_VAR, "unit-test-secret")


@pytest.fixture
def vault(tmp_path):
    vault = ProtectedVault(tmp_path / "vault.db")
    vault.initialise()

    yield vault

    vault.close()


@pytest.fixture
def manager(vault):
    return TokenManager(vault=vault)


def test_token_prefix_groups_equivalent_types():
    assert token_prefix("name") == "PERSON"
    assert token_prefix("PERSON_NAME") == "PERSON"
    assert token_prefix("email_address") == "EMAIL"
    assert token_prefix("phone number") == "PHONE"


def test_token_prefix_falls_back_for_unknown_types():
    assert token_prefix("licence plate") == "LICENCEPLATE"
    assert token_prefix("") == "ENTITY"
    assert token_prefix("123") == "ENTITY"


def test_create_token_has_a_valid_shape():
    token = create_token("EMAIL")

    assert token.startswith("EMAIL_")
    assert is_valid_token(token)


def test_created_tokens_are_unique():
    tokens = {create_token("PERSON") for _ in range(100)}

    assert len(tokens) == 100


def test_is_valid_token_rejects_raw_values():
    assert is_valid_token("john.smith@example.com") is False
    assert is_valid_token("John Smith") is False
    assert is_valid_token("PERSON") is False
    assert is_valid_token(None) is False


def test_normalise_value_ignores_phone_formatting():
    assert normalise_value(
        "PHONE",
        "(555) 123-4567",
    ) == normalise_value("PHONE", "555 123 4567")


def test_normalise_value_ignores_case_and_spacing():
    assert normalise_value(
        "EMAIL",
        " John.Smith@Example.com ",
    ) == "john.smith@example.com"

    assert normalise_value(
        "NAME",
        "John   Smith",
    ) == "john smith"


def test_lookup_hash_is_stable_for_the_same_value():
    first = lookup_hash("EMAIL", "john@example.com")
    second = lookup_hash("EMAIL", "JOHN@example.com ")

    assert first == second


def test_lookup_hash_differs_between_values():
    assert lookup_hash(
        "EMAIL",
        "john@example.com",
    ) != lookup_hash("EMAIL", "jane@example.com")


def test_lookup_hash_hides_the_raw_value():
    digest = lookup_hash("EMAIL", "john@example.com")

    assert "john" not in digest
    assert len(digest) == 64


def test_lookup_hash_depends_on_the_secret(monkeypatch):
    monkeypatch.setenv(SECRET_ENV_VAR, "secret-one")
    first = lookup_hash("EMAIL", "john@example.com")

    monkeypatch.setenv(SECRET_ENV_VAR, "secret-two")
    second = lookup_hash("EMAIL", "john@example.com")

    assert first != second


def test_encryption_key_is_derived_from_the_secret(
    monkeypatch,
):
    monkeypatch.setenv(SECRET_ENV_VAR, "secret-one")
    first = encryption_key()

    assert first == encryption_key()
    assert b"secret-one" not in first

    monkeypatch.setenv(SECRET_ENV_VAR, "secret-two")

    assert encryption_key() != first


def test_encryption_key_differs_from_the_digest_key():
    """One secret, two purposes, two separate derived keys."""
    assert encryption_key() != secret_key()


def test_resolve_or_create_token_stores_the_mapping(
    manager,
    vault,
):
    entity = DetectedPII(
        pii_type="EMAIL",
        original_value="john@example.com",
        placeholder="[EMAIL_1]",
    )

    mapping = manager.resolve_or_create_token(entity)

    assert isinstance(mapping, TokenMapping)
    assert is_valid_token(mapping.token)
    assert vault.list_tokens() == [mapping.token]


def test_recurring_entity_reuses_its_token(manager):
    first = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="PHONE",
            original_value="(555) 123-4567",
            placeholder="[PHONE_1]",
        )
    )

    second = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="phone_number",
            original_value="555-123-4567",
            placeholder="[PHONE_2]",
        )
    )

    assert first.token == second.token


def test_different_entities_get_different_tokens(manager):
    first = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="NAME",
            original_value="John Smith",
            placeholder="[PERSON_1]",
        )
    )

    second = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="NAME",
            original_value="Jane Smith",
            placeholder="[PERSON_2]",
        )
    )

    assert first.token != second.token


def test_tokens_are_reused_across_sessions(vault):
    entity = DetectedPII(
        pii_type="NAME",
        original_value="John Smith",
        placeholder="[PERSON_1]",
    )

    first_session = TokenManager(vault=vault)
    second_session = TokenManager(vault=vault)

    assert (
        first_session.resolve_or_create_token(entity).token
        == second_session.resolve_or_create_token(entity).token
    )


def test_resolve_or_create_token_rejects_empty_values(manager):
    with pytest.raises(ValueError):
        manager.resolve_or_create_token(
            DetectedPII(
                pii_type="NAME",
                original_value="   ",
                placeholder="[PERSON_1]",
            )
        )


def test_resolve_or_create_token_rejects_unsafe_tokens(vault):
    manager = TokenManager(
        vault=vault,
        token_factory=lambda pii_type: "john@example.com",
    )

    with pytest.raises(ValueError):
        manager.resolve_or_create_token(
            DetectedPII(
                pii_type="EMAIL",
                original_value="john@example.com",
                placeholder="[EMAIL_1]",
            )
        )


def test_custom_token_factory_is_used(vault):
    manager = TokenManager(
        vault=vault,
        token_factory=lambda pii_type: "PERSON_ABCDEF123456",
    )

    mapping = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="NAME",
            original_value="John Smith",
            placeholder="[PERSON_1]",
        )
    )

    assert mapping.token == "PERSON_ABCDEF123456"


def test_token_for_value_finds_known_entities(manager):
    mapping = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="EMAIL",
            original_value="john@example.com",
            placeholder="[EMAIL_1]",
        )
    )

    assert (
        manager.token_for_value("EMAIL", "John@Example.com")
        == mapping.token
    )
    assert (
        manager.token_for_value("EMAIL", "jane@example.com")
        is None
    )


def test_resolve_token_returns_the_protected_identity(
    manager,
    vault,
):
    mapping = manager.resolve_or_create_token(
        DetectedPII(
            pii_type="NAME",
            original_value="John Smith",
            placeholder="[PERSON_1]",
        )
    )

    resolved = manager.resolve_token(
        mapping.token,
        actor="operator",
        reason="support request",
    )

    assert resolved is not None
    assert resolved.original_value == "John Smith"

    history = vault.access_history(token=mapping.token)

    assert history[0]["actor"] == "operator"
    assert history[0]["was_found"] is True


def test_resolve_token_returns_none_for_unknown_tokens(manager):
    assert (
        manager.resolve_token("PERSON_000000000000") is None
    )


def test_module_level_helper_uses_the_shared_vault(
    tmp_path,
    monkeypatch,
):
    from robopii import storage

    storage.configure_storage(data_dir=tmp_path)
    monkeypatch.setenv(SECRET_ENV_VAR, "unit-test-secret")

    try:
        entity = DetectedPII(
            pii_type="EMAIL",
            original_value="john@example.com",
            placeholder="[EMAIL_1]",
        )

        first = resolve_or_create_token(entity)
        second = resolve_or_create_token(entity)

        assert first.token == second.token
        assert storage.get_protected_vault().list_tokens() == [
            first.token
        ]
    finally:
        storage.configure_storage()
