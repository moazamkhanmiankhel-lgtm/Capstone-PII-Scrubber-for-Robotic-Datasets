"""Separated primary and protected storage.

RoboPII keeps two independent SQLite databases:

``primary.db``
    Scrubbed records: masked transcripts, scrubbed media paths and the
    pseudonymised tokens that link records together. No raw PII.

``vault.db``
    The protected mapping between a token and the identity value it
    stands for, held encrypted, plus an access log of every lookup.

Keeping the two in separate files means the primary robot memory can be
copied, shared or inspected without carrying the identity mappings with
it.
"""

import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from robopii.models import TokenMapping
from robopii.token_manager import (
    is_valid_token,
    lookup_hash,
    reset_token_manager,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

DATA_DIR_ENV_VAR = "ROBOPII_DATA_DIR"
PRIMARY_DB_ENV_VAR = "ROBOPII_PRIMARY_DB"
VAULT_DB_ENV_VAR = "ROBOPII_VAULT_DB"

PRIMARY_DB_NAME = "primary.db"
VAULT_DB_NAME = "vault.db"

SCHEMA_VERSION = 2

DATABASE_FILE_MODE = 0o600

PRIMARY_RECORD_FIELDS = frozenset(
    {
        "record_id",
        "created_at",
        "scrubbed_media_path",
        "scrubbed_transcript",
        "source_label",
        "metadata",
        "tokens",
    }
)

# Defensive guard only. These patterns catch the obvious raw values that
# should have been replaced upstream; they cannot recognise a plain name.
RAW_PII_PATTERNS = {
    "email address": re.compile(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ),
    "government id number": re.compile(
        r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"
    ),
    "phone number": re.compile(
        r"(?<![\w.-])(?:\+?\d{1,3}[ .-]?)?"
        r"(?:\(\d{2,4}\)|\d{2,4})(?:[ .-]\d{2,4}){2,}(?![\w-])"
    ),
    "long digit sequence": re.compile(
        r"(?<![\w-])\d{10,}(?![\w-])"
    ),
}

MINIMUM_DIGITS = {
    "phone number": 9,
    "long digit sequence": 10,
}

_data_dir_override: Path | None = None
_primary_db_override: Path | None = None
_vault_db_override: Path | None = None

_primary_store = None
_protected_vault = None


class StorageError(RuntimeError):
    """Raised when a record cannot be stored or read."""


class RawPIIError(ValueError):
    """Raised when raw PII is about to enter the primary store."""


def utc_now() -> str:
    """Return the current UTC time as a sortable ISO string."""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    )


def resolve_data_dir() -> Path:
    """Return the directory holding the RoboPII databases."""
    if _data_dir_override is not None:
        return _data_dir_override

    configured_dir = os.environ.get(DATA_DIR_ENV_VAR)

    if configured_dir:
        return Path(configured_dir).expanduser()

    return DEFAULT_DATA_DIR


def resolve_primary_db_path() -> Path:
    """Return the path of the primary database file."""
    if _primary_db_override is not None:
        return _primary_db_override

    configured_path = os.environ.get(PRIMARY_DB_ENV_VAR)

    if configured_path:
        return Path(configured_path).expanduser()

    return resolve_data_dir() / PRIMARY_DB_NAME


def resolve_vault_db_path() -> Path:
    """Return the path of the protected vault database file."""
    if _vault_db_override is not None:
        return _vault_db_override

    configured_path = os.environ.get(VAULT_DB_ENV_VAR)

    if configured_path:
        return Path(configured_path).expanduser()

    return resolve_data_dir() / VAULT_DB_NAME


def _iter_strings(value: Any) -> Iterator[str]:
    """Yield every string inside a nested record value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_strings(item)


def _count_digits(text: str) -> int:
    """Return how many digits a string contains."""
    return sum(1 for character in text if character.isdigit())


def find_raw_pii(text: str) -> str | None:
    """Return the kind of raw PII found in a string, if any."""
    for label, pattern in RAW_PII_PATTERNS.items():
        minimum_digits = MINIMUM_DIGITS.get(label)

        for match in pattern.finditer(text):
            if (
                minimum_digits is not None
                and _count_digits(match.group())
                < minimum_digits
            ):
                continue

            return label

    return None


def validate_primary_record(record: Mapping[str, Any]) -> None:
    """Reject anything that must not enter the primary store.

    Raises:
        ValueError: the record has unknown fields or invalid tokens.
        RawPIIError: the record still contains obvious raw PII.
    """
    if not isinstance(record, Mapping):
        raise ValueError(
            "A primary record must be a mapping."
        )

    unknown_fields = sorted(
        set(record) - PRIMARY_RECORD_FIELDS
    )

    if unknown_fields:
        raise ValueError(
            "Unknown primary record fields: "
            f"{', '.join(unknown_fields)}"
        )

    tokens = record.get("tokens") or []

    if isinstance(tokens, (str, bytes)) or not isinstance(
        tokens, Sequence
    ):
        raise ValueError(
            "Record tokens must be a list of token strings."
        )

    for token in tokens:
        if not is_valid_token(token):
            raise ValueError(
                f"Record token is not a valid token: {token!r}"
            )

    metadata = record.get("metadata")

    if metadata is not None and not isinstance(
        metadata, Mapping
    ):
        raise ValueError(
            "Record metadata must be a mapping."
        )

    scanned_fields = (
        "scrubbed_media_path",
        "scrubbed_transcript",
        "source_label",
        "metadata",
    )

    for field_name in scanned_fields:
        for text in _iter_strings(record.get(field_name)):
            found = find_raw_pii(text)

            if found is not None:
                raise RawPIIError(
                    f"Refusing to store raw PII in the primary "
                    f"database: {field_name} contains what looks "
                    f"like a {found}."
                )


class _SQLiteStore:
    """Small helper shared by the primary store and the vault."""

    schema: tuple[str, ...] = ()

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open connection, creating it on first use."""
        if self._connection is None:
            self.db_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._connection = sqlite3.connect(
                str(self.db_path)
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute(
                "PRAGMA foreign_keys = ON"
            )

            self._restrict_file_permissions()

        return self._connection

    def _restrict_file_permissions(self) -> None:
        """Keep the database readable only by its owner."""
        try:
            self.db_path.chmod(DATABASE_FILE_MODE)
        except OSError:
            pass

    def initialise(self) -> None:
        """Create the tables if they do not exist yet."""
        with self.connection as connection:
            for statement in self.schema:
                connection.execute(statement)

            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version) "
                "VALUES (?)",
                (SCHEMA_VERSION,),
            )

        self._restrict_file_permissions()

    def schema_version(self) -> int:
        """Return the highest schema version applied."""
        row = self.connection.execute(
            "SELECT MAX(version) AS version FROM schema_version"
        ).fetchone()

        return 0 if row["version"] is None else row["version"]

    def table_names(self) -> list[str]:
        """Return the tables present in this database."""
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' ORDER BY name"
        ).fetchall()

        return [row["name"] for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class PrimaryStore(_SQLiteStore):
    """Scrubbed records and their pseudonymised tokens."""

    schema = (
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            scrubbed_media_path TEXT,
            scrubbed_transcript TEXT,
            source_label TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS record_tokens (
            record_id TEXT NOT NULL
                REFERENCES records (record_id) ON DELETE CASCADE,
            token TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (record_id, token)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_record_tokens_token
            ON record_tokens (token)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_records_created_at
            ON records (created_at)
        """,
    )

    def save_record(self, record: Mapping[str, Any]) -> str:
        """Validate and store a scrubbed record.

        Returns:
            The record ID of the stored record.
        """
        validate_primary_record(record)

        record_id = str(record.get("record_id") or uuid4())
        created_at = record.get("created_at") or utc_now()

        try:
            metadata = json.dumps(
                dict(record.get("metadata") or {}),
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Record metadata is not JSON serialisable: {error}"
            ) from error

        tokens = list(
            dict.fromkeys(record.get("tokens") or [])
        )

        try:
            with self.connection as connection:
                connection.execute(
                    """
                    INSERT INTO records (
                        record_id,
                        created_at,
                        scrubbed_media_path,
                        scrubbed_transcript,
                        source_label,
                        metadata
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        created_at,
                        record.get("scrubbed_media_path"),
                        record.get("scrubbed_transcript"),
                        record.get("source_label"),
                        metadata,
                    ),
                )

                connection.executemany(
                    """
                    INSERT OR IGNORE INTO record_tokens (
                        record_id,
                        token,
                        position
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (record_id, token, position)
                        for position, token in enumerate(tokens)
                    ],
                )
        except sqlite3.IntegrityError as error:
            raise StorageError(
                f"Could not store record {record_id}: {error}"
            ) from error

        return record_id

    def _tokens_for_record(self, record_id: str) -> list[str]:
        """Return the tokens linked to a record, in order."""
        rows = self.connection.execute(
            """
            SELECT token FROM record_tokens
            WHERE record_id = ?
            ORDER BY position
            """,
            (record_id,),
        ).fetchall()

        return [row["token"] for row in rows]

    def _row_to_record(
        self,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        """Convert a database row into a record dictionary."""
        return {
            "record_id": row["record_id"],
            "created_at": row["created_at"],
            "scrubbed_media_path": row["scrubbed_media_path"],
            "scrubbed_transcript": row["scrubbed_transcript"],
            "source_label": row["source_label"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "tokens": self._tokens_for_record(
                row["record_id"]
            ),
        }

    def get_record(
        self,
        record_id: str,
    ) -> dict[str, Any] | None:
        """Return one scrubbed record, or None when it is unknown."""
        row = self.connection.execute(
            "SELECT * FROM records WHERE record_id = ?",
            (record_id,),
        ).fetchone()

        return None if row is None else self._row_to_record(row)

    def find_records_by_token(
        self,
        token: str,
    ) -> list[dict[str, Any]]:
        """Return the scrubbed records linked to a token."""
        if not token:
            return []

        rows = self.connection.execute(
            """
            SELECT records.* FROM records
            JOIN record_tokens
                ON record_tokens.record_id = records.record_id
            WHERE record_tokens.token = ?
            ORDER BY records.created_at DESC, records.rowid DESC
            """,
            (token,),
        ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def list_records(
        self,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return stored records, newest first."""
        query = (
            "SELECT * FROM records "
            "ORDER BY created_at DESC, rowid DESC"
        )
        parameters: tuple[Any, ...] = ()

        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)

        rows = self.connection.execute(
            query,
            parameters,
        ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def count_records(self) -> int:
        """Return how many records are stored."""
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM records"
        ).fetchone()

        return row["total"]


class ProtectedVault(_SQLiteStore):
    """Protected token to identity mappings and their access log."""

    schema = (
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS identity_mappings (
            token TEXT PRIMARY KEY,
            pii_type TEXT NOT NULL,
            encrypted_value BLOB NOT NULL,
            lookup_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS access_log (
            access_id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'lookup',
            was_found INTEGER NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_access_log_token
            ON access_log (token)
        """,
    )

    def initialise(self) -> None:
        """Create the vault tables, refusing an unencrypted vault."""
        self._reject_unencrypted_vault()
        super().initialise()

    def _reject_unencrypted_vault(self) -> None:
        """Stop an older plaintext vault being used by mistake."""
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(identity_mappings)"
            )
        }

        if "original_value" in columns:
            raise StorageError(
                f"The vault at {self.db_path} was created before "
                "identity values were encrypted. Delete the file and "
                "let it be recreated; its contents cannot be "
                "migrated because the plaintext is not re-encryptable "
                "without re-reading every value."
            )

    @property
    def _cipher(self):
        """Return the cipher used for identity values at rest."""
        from cryptography.fernet import Fernet

        from robopii.token_manager import encryption_key

        return Fernet(encryption_key())

    def _encrypt(self, original_value: str) -> bytes:
        """Encrypt an identity value for storage."""
        return self._cipher.encrypt(
            str(original_value).encode("utf-8")
        )

    def _decrypt(self, encrypted_value: bytes) -> str:
        """Decrypt a stored identity value."""
        from cryptography.fernet import InvalidToken

        try:
            return self._cipher.decrypt(
                bytes(encrypted_value)
            ).decode("utf-8")
        except InvalidToken as error:
            raise StorageError(
                "Could not decrypt a stored identity value. The vault "
                "secret does not match the one used to write it: "
                "check ROBOPII_VAULT_SECRET and data/vault.key."
            ) from error

    @staticmethod
    def _as_mapping(
        mapping: TokenMapping | Mapping[str, Any],
    ) -> TokenMapping:
        """Accept either a TokenMapping or a plain dictionary."""
        if isinstance(mapping, TokenMapping):
            return mapping

        if not isinstance(mapping, Mapping):
            raise ValueError(
                "A protected mapping must be a TokenMapping or "
                "a mapping."
            )

        missing_fields = sorted(
            {"token", "pii_type", "original_value"}
            - set(mapping)
        )

        if missing_fields:
            raise ValueError(
                "Protected mapping is missing fields: "
                f"{', '.join(missing_fields)}"
            )

        return TokenMapping(
            token=mapping["token"],
            pii_type=mapping["pii_type"],
            original_value=mapping["original_value"],
        )

    def save_mapping(
        self,
        mapping: TokenMapping | Mapping[str, Any],
    ) -> TokenMapping:
        """Store a token to identity mapping.

        Saving the same identity twice is safe: the first token is kept
        and only the last seen timestamp changes.

        Returns:
            The mapping now held in the vault, which may use an earlier
            token for this identity.
        """
        token_mapping = self._as_mapping(mapping)

        if not is_valid_token(token_mapping.token):
            raise ValueError(
                f"Invalid token: {token_mapping.token!r}"
            )

        if not str(token_mapping.original_value).strip():
            raise ValueError(
                "A protected mapping needs an original value."
            )

        digest = lookup_hash(
            token_mapping.pii_type,
            token_mapping.original_value,
        )

        timestamp = utc_now()

        with self.connection as connection:
            existing_digest = connection.execute(
                "SELECT lookup_hash FROM identity_mappings "
                "WHERE token = ?",
                (token_mapping.token,),
            ).fetchone()

            if (
                existing_digest is not None
                and existing_digest["lookup_hash"] != digest
            ):
                raise StorageError(
                    f"Token {token_mapping.token} is already "
                    "mapped to a different identity."
                )

            connection.execute(
                """
                INSERT INTO identity_mappings (
                    token,
                    pii_type,
                    encrypted_value,
                    lookup_hash,
                    created_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (lookup_hash) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    token_mapping.token,
                    token_mapping.pii_type,
                    self._encrypt(token_mapping.original_value),
                    digest,
                    timestamp,
                    timestamp,
                ),
            )

        stored_mapping = self.find_mapping_by_lookup_hash(digest)

        if stored_mapping is None:
            raise StorageError(
                "Protected mapping could not be stored."
            )

        return stored_mapping

    def _row_to_mapping(self, row: sqlite3.Row) -> TokenMapping:
        """Convert a vault row into a TokenMapping, decrypting it."""
        return TokenMapping(
            token=row["token"],
            pii_type=row["pii_type"],
            original_value=self._decrypt(row["encrypted_value"]),
        )

    def find_mapping_by_lookup_hash(
        self,
        digest: str,
    ) -> TokenMapping | None:
        """Return the mapping stored under a lookup digest."""
        row = self.connection.execute(
            "SELECT * FROM identity_mappings "
            "WHERE lookup_hash = ?",
            (digest,),
        ).fetchone()

        return None if row is None else self._row_to_mapping(row)

    def get_mapping(
        self,
        token: str,
        actor: str = "unknown",
        reason: str = "unspecified",
    ) -> TokenMapping | None:
        """Return the identity behind a token and log the access."""
        row = self.connection.execute(
            "SELECT * FROM identity_mappings WHERE token = ?",
            (token,),
        ).fetchone()

        self._log_access(
            token=token,
            actor=actor,
            reason=reason,
            action="lookup",
            was_found=row is not None,
        )

        return None if row is None else self._row_to_mapping(row)

    def _log_access(
        self,
        token: str,
        actor: str,
        reason: str,
        action: str,
        was_found: bool,
    ) -> None:
        """Record one attempt to read or remove an identity."""
        with self.connection as connection:
            connection.execute(
                """
                INSERT INTO access_log (
                    token,
                    accessed_at,
                    actor,
                    reason,
                    action,
                    was_found
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    utc_now(),
                    actor,
                    reason,
                    action,
                    1 if was_found else 0,
                ),
            )

    def delete_mapping(
        self,
        token: str,
        actor: str = "unknown",
        reason: str = "unspecified",
    ) -> bool:
        """Remove one identity mapping from the vault.

        The scrubbed records that reference the token are left in the
        primary store, where they become permanently unlinkable: with
        the mapping gone, nothing connects the token to a person.

        Returns:
            True when a mapping was removed.
        """
        with self.connection as connection:
            cursor = connection.execute(
                "DELETE FROM identity_mappings WHERE token = ?",
                (token,),
            )
            was_found = cursor.rowcount > 0

        self._log_access(
            token=token,
            actor=actor,
            reason=reason,
            action="delete",
            was_found=was_found,
        )

        return was_found

    def list_tokens(self) -> list[str]:
        """Return every token held in the vault."""
        rows = self.connection.execute(
            "SELECT token FROM identity_mappings "
            "ORDER BY created_at, token"
        ).fetchall()

        return [row["token"] for row in rows]

    def count_mappings(self) -> int:
        """Return how many identity mappings are stored."""
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM identity_mappings"
        ).fetchone()

        return row["total"]

    def access_history(
        self,
        token: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent vault lookups, newest first."""
        query = "SELECT * FROM access_log"
        parameters: tuple[Any, ...] = ()

        if token is not None:
            query += " WHERE token = ?"
            parameters = (token,)

        query += " ORDER BY access_id DESC LIMIT ?"
        parameters += (limit,)

        rows = self.connection.execute(
            query,
            parameters,
        ).fetchall()

        return [
            {
                "access_id": row["access_id"],
                "token": row["token"],
                "accessed_at": row["accessed_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "action": row["action"],
                "was_found": bool(row["was_found"]),
            }
            for row in rows
        ]


def configure_storage(
    data_dir: str | Path | None = None,
    primary_db_path: str | Path | None = None,
    vault_db_path: str | Path | None = None,
) -> None:
    """Point the shared stores at a different location.

    Passing nothing clears every override, so the environment
    variables and the default ``data/`` directory apply again.
    """
    global _data_dir_override
    global _primary_db_override
    global _vault_db_override

    close_databases()

    _data_dir_override = (
        None if data_dir is None else Path(data_dir).expanduser()
    )
    _primary_db_override = (
        None
        if primary_db_path is None
        else Path(primary_db_path).expanduser()
    )
    _vault_db_override = (
        None
        if vault_db_path is None
        else Path(vault_db_path).expanduser()
    )

    reset_token_manager()


def get_primary_store() -> PrimaryStore:
    """Return the shared primary store."""
    global _primary_store

    if _primary_store is None:
        _primary_store = PrimaryStore(
            resolve_primary_db_path()
        )
        _primary_store.initialise()

    return _primary_store


def get_protected_vault() -> ProtectedVault:
    """Return the shared protected vault."""
    global _protected_vault

    if _protected_vault is None:
        _protected_vault = ProtectedVault(
            resolve_vault_db_path()
        )
        _protected_vault.initialise()

    return _protected_vault


def close_databases() -> None:
    """Close the shared databases."""
    global _primary_store
    global _protected_vault

    if _primary_store is not None:
        _primary_store.close()
        _primary_store = None

    if _protected_vault is not None:
        _protected_vault.close()
        _protected_vault = None


def initialise_databases(
    data_dir: str | Path | None = None,
) -> None:
    """Create the primary store and protected vault."""
    if data_dir is not None:
        configure_storage(data_dir=data_dir)

    get_primary_store()
    get_protected_vault()


def save_primary_record(record: Mapping[str, Any]) -> str:
    """Save scrubbed data and return its record ID."""
    return get_primary_store().save_record(record)


def save_protected_mapping(
    mapping: TokenMapping | Mapping[str, Any],
) -> TokenMapping:
    """Save sensitive token mappings in the protected vault."""
    return get_protected_vault().save_mapping(mapping)


def find_records_by_token(token: str) -> list[dict[str, Any]]:
    """Find scrubbed primary records associated with a token."""
    return get_primary_store().find_records_by_token(token)


def get_primary_record(
    record_id: str,
) -> dict[str, Any] | None:
    """Return one scrubbed record from the primary store."""
    return get_primary_store().get_record(record_id)


def get_protected_mapping(
    token: str,
    actor: str = "unknown",
    reason: str = "unspecified",
) -> TokenMapping | None:
    """Return the protected identity behind a token.

    Every call is written to the vault access log.
    """
    return get_protected_vault().get_mapping(
        token,
        actor=actor,
        reason=reason,
    )


def delete_protected_mapping(
    token: str,
    actor: str = "unknown",
    reason: str = "unspecified",
) -> bool:
    """Forget the identity behind a token.

    The scrubbed records stay in the primary store and become
    permanently unlinkable.
    """
    return get_protected_vault().delete_mapping(
        token,
        actor=actor,
        reason=reason,
    )
