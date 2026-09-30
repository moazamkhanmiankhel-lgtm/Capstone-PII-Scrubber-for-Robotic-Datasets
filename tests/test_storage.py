import pytest

from robopii import storage
from robopii.models import TokenMapping
from robopii.storage import (
    PrimaryStore,
    ProtectedVault,
    RawPIIError,
    StorageError,
)
from robopii.token_manager import SECRET_ENV_VAR


PERSON_TOKEN = "PERSON_A1B2C3D4E5F6"
EMAIL_TOKEN = "EMAIL_0123456789AB"


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    """Use a predictable vault secret in every test."""
    monkeypatch.setenv(SECRET_ENV_VAR, "unit-test-secret")


@pytest.fixture
def primary(tmp_path):
    store = PrimaryStore(tmp_path / "primary.db")
    store.initialise()

    yield store

    store.close()


@pytest.fixture
def vault(tmp_path):
    protected_vault = ProtectedVault(tmp_path / "vault.db")
    protected_vault.initialise()

    yield protected_vault

    protected_vault.close()


@pytest.fixture
def shared_storage(tmp_path):
    """Point the module level helpers at a temporary directory."""
    storage.configure_storage(data_dir=tmp_path)

    yield tmp_path

    storage.configure_storage()


def scrubbed_record(**overrides):
    """Return a valid scrubbed record for tests."""
    record = {
        "scrubbed_media_path": "output/scrubbed_frames/clip_01",
        "scrubbed_transcript": (
            "[PERSON_1] asked the robot for directions."
        ),
        "tokens": [PERSON_TOKEN],
    }
    record.update(overrides)

    return record


def test_initialise_creates_two_separate_databases(
    shared_storage,
):
    storage.initialise_databases()

    assert (shared_storage / "primary.db").is_file()
    assert (shared_storage / "vault.db").is_file()


def test_primary_database_holds_no_identity_tables(primary):
    assert "records" in primary.table_names()
    assert "record_tokens" in primary.table_names()
    assert "identity_mappings" not in primary.table_names()


def test_vault_holds_no_scrubbed_records(vault):
    assert "identity_mappings" in vault.table_names()
    assert "access_log" in vault.table_names()
    assert "records" not in vault.table_names()


def test_schema_version_is_recorded(primary, vault):
    assert primary.schema_version() == storage.SCHEMA_VERSION
    assert vault.schema_version() == storage.SCHEMA_VERSION


def test_save_record_returns_an_id_and_round_trips(primary):
    record_id = primary.save_record(scrubbed_record())

    stored = primary.get_record(record_id)

    assert stored["record_id"] == record_id
    assert stored["tokens"] == [PERSON_TOKEN]
    assert stored["created_at"]
    assert stored["metadata"] == {}
    assert (
        stored["scrubbed_transcript"]
        == "[PERSON_1] asked the robot for directions."
    )


def test_metadata_round_trips(primary):
    record_id = primary.save_record(
        scrubbed_record(
            metadata={"faces_detected": 2, "source": "demo"}
        )
    )

    stored = primary.get_record(record_id)

    assert stored["metadata"]["faces_detected"] == 2


def test_get_record_returns_none_when_unknown(primary):
    assert primary.get_record("missing-id") is None


def test_find_records_by_token_links_repeat_visits(primary):
    first_id = primary.save_record(
        scrubbed_record(created_at="2026-01-01T09:00:00+00:00")
    )
    second_id = primary.save_record(
        scrubbed_record(created_at="2026-02-01T09:00:00+00:00")
    )
    primary.save_record(
        scrubbed_record(tokens=[EMAIL_TOKEN])
    )

    found = primary.find_records_by_token(PERSON_TOKEN)

    assert [record["record_id"] for record in found] == [
        second_id,
        first_id,
    ]


def test_records_stored_in_one_moment_keep_their_order(
    primary,
):
    first_id = primary.save_record(scrubbed_record())
    second_id = primary.save_record(scrubbed_record())

    found = primary.find_records_by_token(PERSON_TOKEN)

    assert [record["record_id"] for record in found] == [
        second_id,
        first_id,
    ]


def test_find_records_by_unknown_token_returns_nothing(primary):
    primary.save_record(scrubbed_record())

    assert primary.find_records_by_token(EMAIL_TOKEN) == []
    assert primary.find_records_by_token("") == []


def test_repeated_tokens_are_stored_once(primary):
    record_id = primary.save_record(
        scrubbed_record(
            tokens=[PERSON_TOKEN, PERSON_TOKEN, EMAIL_TOKEN]
        )
    )

    stored = primary.get_record(record_id)

    assert stored["tokens"] == [PERSON_TOKEN, EMAIL_TOKEN]


def test_list_records_returns_newest_first(primary):
    older = primary.save_record(
        scrubbed_record(created_at="2026-01-01T09:00:00+00:00")
    )
    newer = primary.save_record(
        scrubbed_record(created_at="2026-03-01T09:00:00+00:00")
    )

    listed = primary.list_records()

    assert [record["record_id"] for record in listed] == [
        newer,
        older,
    ]


def test_duplicate_record_ids_are_rejected(primary):
    primary.save_record(scrubbed_record(record_id="fixed-id"))

    with pytest.raises(StorageError):
        primary.save_record(
            scrubbed_record(record_id="fixed-id")
        )


def test_email_in_a_transcript_is_rejected(primary):
    with pytest.raises(RawPIIError):
        primary.save_record(
            scrubbed_record(
                scrubbed_transcript=(
                    "Contact me on john.smith@example.com"
                )
            )
        )

    assert primary.count_records() == 0


def test_phone_number_in_a_media_path_is_rejected(primary):
    with pytest.raises(RawPIIError):
        primary.save_record(
            scrubbed_record(
                scrubbed_media_path=(
                    "output/frames/555-123-4567.mp4"
                )
            )
        )


def test_raw_pii_in_metadata_is_rejected(primary):
    with pytest.raises(RawPIIError):
        primary.save_record(
            scrubbed_record(
                metadata={"notes": "reached on 4155551234"}
            )
        )


def test_harmless_numbers_are_accepted(primary):
    record_id = primary.save_record(
        scrubbed_record(
            scrubbed_transcript=(
                "On 2026-05-01 at 10:30 [PERSON_1] collected "
                "order 12345678 from bay 7."
            ),
            scrubbed_media_path=(
                "output/scrubbed_frames/clip_000123"
            ),
        )
    )

    assert primary.get_record(record_id) is not None


def test_unknown_record_fields_are_rejected(primary):
    with pytest.raises(ValueError):
        primary.save_record(
            scrubbed_record(speaker_name="John Smith")
        )


def test_values_that_are_not_tokens_are_rejected(primary):
    with pytest.raises(ValueError):
        primary.save_record(
            scrubbed_record(tokens=["john@example.com"])
        )

    with pytest.raises(ValueError):
        primary.save_record(scrubbed_record(tokens=PERSON_TOKEN))


def test_save_mapping_round_trips(vault):
    stored = vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    assert stored.token == PERSON_TOKEN

    found = vault.get_mapping(PERSON_TOKEN)

    assert found.original_value == "John Smith"
    assert found.pii_type == "NAME"


def test_save_mapping_accepts_a_dictionary(vault):
    vault.save_mapping(
        {
            "token": EMAIL_TOKEN,
            "pii_type": "EMAIL",
            "original_value": "john@example.com",
        }
    )

    assert vault.list_tokens() == [EMAIL_TOKEN]


def test_save_mapping_keeps_the_first_token_for_an_identity(
    vault,
):
    vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    stored = vault.save_mapping(
        TokenMapping(
            token="PERSON_FFFFFFFFFFFF",
            pii_type="NAME",
            original_value="john smith",
        )
    )

    assert stored.token == PERSON_TOKEN
    assert vault.count_mappings() == 1


def test_a_token_cannot_be_reused_for_another_identity(vault):
    vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    with pytest.raises(StorageError):
        vault.save_mapping(
            TokenMapping(
                token=PERSON_TOKEN,
                pii_type="NAME",
                original_value="Jane Doe",
            )
        )


def test_save_mapping_rejects_invalid_input(vault):
    with pytest.raises(ValueError):
        vault.save_mapping(
            TokenMapping(
                token="not-a-token",
                pii_type="NAME",
                original_value="John Smith",
            )
        )

    with pytest.raises(ValueError):
        vault.save_mapping(
            TokenMapping(
                token=PERSON_TOKEN,
                pii_type="NAME",
                original_value="  ",
            )
        )

    with pytest.raises(ValueError):
        vault.save_mapping({"token": PERSON_TOKEN})


def test_every_vault_lookup_is_logged(vault):
    vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    vault.get_mapping(
        PERSON_TOKEN,
        actor="operator",
        reason="returning visitor",
    )
    vault.get_mapping("PERSON_000000000000")

    history = vault.access_history()

    assert len(history) == 2
    assert history[0]["was_found"] is False
    assert history[1]["actor"] == "operator"
    assert history[1]["reason"] == "returning visitor"


def test_unknown_tokens_resolve_to_nothing(vault):
    assert vault.get_mapping("PERSON_000000000000") is None


def test_raw_identity_is_unreadable_in_both_databases(
    shared_storage,
):
    storage.initialise_databases()

    storage.save_protected_mapping(
        {
            "token": EMAIL_TOKEN,
            "pii_type": "EMAIL",
            "original_value": "john.smith@example.com",
        }
    )
    storage.save_primary_record(
        scrubbed_record(
            scrubbed_transcript=(
                "[PERSON_1] left an address at [EMAIL_1]."
            ),
            tokens=[PERSON_TOKEN, EMAIL_TOKEN],
        )
    )

    storage.close_databases()

    primary_bytes = (shared_storage / "primary.db").read_bytes()
    vault_bytes = (shared_storage / "vault.db").read_bytes()

    # Absent from the primary store by design, and unreadable at rest
    # in the vault because it is encrypted.
    assert b"john.smith@example.com" not in primary_bytes
    assert b"john.smith@example.com" not in vault_bytes
    assert EMAIL_TOKEN.encode() in primary_bytes

    # It is still recoverable through an authorised lookup.
    recovered = storage.get_protected_mapping(
        EMAIL_TOKEN,
        actor="test",
        reason="verifying round trip",
    )

    assert recovered.original_value == "john.smith@example.com"


def test_identity_values_are_encrypted_at_rest(vault):
    vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    stored = vault.connection.execute(
        "SELECT encrypted_value FROM identity_mappings"
    ).fetchone()["encrypted_value"]

    assert b"John Smith" not in bytes(stored)
    assert (
        vault.get_mapping(PERSON_TOKEN).original_value
        == "John Smith"
    )


def test_a_different_secret_cannot_read_the_vault(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "vault.db"

    first_vault = ProtectedVault(db_path)
    first_vault.initialise()
    first_vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )
    first_vault.close()

    monkeypatch.setenv(SECRET_ENV_VAR, "a-different-secret")

    second_vault = ProtectedVault(db_path)

    with pytest.raises(StorageError):
        second_vault.get_mapping(PERSON_TOKEN)

    second_vault.close()


def test_encryption_does_not_break_token_reuse(vault):
    first = vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )
    second = vault.save_mapping(
        TokenMapping(
            token="PERSON_FFFFFFFFFFFF",
            pii_type="NAME",
            original_value="john smith",
        )
    )

    assert first.token == second.token
    assert vault.count_mappings() == 1


def test_a_plaintext_vault_is_refused(tmp_path):
    import sqlite3

    db_path = tmp_path / "old_vault.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE identity_mappings (
            token TEXT PRIMARY KEY,
            pii_type TEXT NOT NULL,
            original_value TEXT NOT NULL,
            lookup_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(StorageError):
        ProtectedVault(db_path).initialise()


def test_deleting_a_mapping_leaves_records_unlinkable(
    shared_storage,
):
    storage.initialise_databases()

    storage.save_protected_mapping(
        {
            "token": PERSON_TOKEN,
            "pii_type": "NAME",
            "original_value": "John Smith",
        }
    )
    record_id = storage.save_primary_record(scrubbed_record())

    removed = storage.delete_protected_mapping(
        PERSON_TOKEN,
        actor="operator",
        reason="withdrawal of consent",
    )

    assert removed is True
    assert (
        storage.get_protected_mapping(PERSON_TOKEN) is None
    )

    # The scrubbed record survives, but nothing connects its token
    # to a person any more.
    remaining = storage.find_records_by_token(PERSON_TOKEN)

    assert [record["record_id"] for record in remaining] == [
        record_id
    ]


def test_deletion_is_recorded_in_the_access_log(vault):
    vault.save_mapping(
        TokenMapping(
            token=PERSON_TOKEN,
            pii_type="NAME",
            original_value="John Smith",
        )
    )

    vault.delete_mapping(
        PERSON_TOKEN,
        actor="operator",
        reason="withdrawal of consent",
    )

    entry = vault.access_history(token=PERSON_TOKEN)[0]

    assert entry["action"] == "delete"
    assert entry["actor"] == "operator"
    assert entry["was_found"] is True


def test_deleting_an_unknown_mapping_reports_nothing_removed(
    vault,
):
    assert vault.delete_mapping("PERSON_000000000000") is False
    assert (
        vault.access_history()[0]["was_found"] is False
    )


def test_module_level_helpers_share_one_location(
    shared_storage,
):
    storage.initialise_databases()

    record_id = storage.save_primary_record(scrubbed_record())

    found = storage.find_records_by_token(PERSON_TOKEN)

    assert [record["record_id"] for record in found] == [
        record_id
    ]
    assert (
        storage.get_primary_record(record_id)["tokens"]
        == [PERSON_TOKEN]
    )


def test_retrieval_of_context_behind_a_token(shared_storage):
    storage.initialise_databases()

    storage.save_protected_mapping(
        {
            "token": PERSON_TOKEN,
            "pii_type": "NAME",
            "original_value": "John Smith",
        }
    )
    storage.save_primary_record(scrubbed_record())

    identity = storage.get_protected_mapping(
        PERSON_TOKEN,
        actor="researcher",
        reason="demo lookup",
    )
    context = storage.find_records_by_token(PERSON_TOKEN)

    assert identity.original_value == "John Smith"
    assert len(context) == 1


def test_configure_storage_accepts_explicit_paths(tmp_path):
    storage.configure_storage(
        primary_db_path=tmp_path / "custom_primary.db",
        vault_db_path=tmp_path / "nested" / "custom_vault.db",
    )

    try:
        storage.initialise_databases()

        assert (tmp_path / "custom_primary.db").is_file()
        assert (
            tmp_path / "nested" / "custom_vault.db"
        ).is_file()
    finally:
        storage.configure_storage()


def test_database_files_are_owner_only(shared_storage):
    storage.initialise_databases()

    vault_mode = (shared_storage / "vault.db").stat().st_mode

    assert vault_mode & 0o077 == 0
