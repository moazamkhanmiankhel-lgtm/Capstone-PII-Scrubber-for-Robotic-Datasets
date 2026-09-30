"""Pseudonymised token generation and resolution.

Tokens are the only identity reference allowed inside the primary store.
A token looks like ``PERSON_A1B2C3D4E5F6`` and carries no information about
the person it stands for.

Recurring entities reuse the same token. The manager finds the existing
token with a keyed HMAC-SHA256 digest of the normalised PII value, so the
vault can be searched without holding a plaintext index of raw values.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Callable
from pathlib import Path

from robopii.models import DetectedPII, TokenMapping


SECRET_ENV_VAR = "ROBOPII_VAULT_SECRET"
SECRET_FILE_NAME = "vault.key"

ENCRYPTION_KEY_LABEL = b"robopii-vault-encryption"

TOKEN_HEX_CHARACTERS = 12

TOKEN_PATTERN = re.compile(
    r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_[0-9A-F]{8,32}$"
)

# Different wordings of the same PII type share one token prefix so that
# the text scrubber and the face recognizer stay interoperable.
TOKEN_PREFIXES = {
    "PERSON": "PERSON",
    "NAME": "PERSON",
    "PERSON_NAME": "PERSON",
    "FACE": "PERSON",
    "SPEAKER": "PERSON",
    "EMAIL": "EMAIL",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE": "PHONE",
    "PHONE_NUMBER": "PHONE",
    "ADDRESS": "ADDRESS",
    "LOCATION": "LOCATION",
    "ORGANISATION": "ORG",
    "ORGANIZATION": "ORG",
}

DEFAULT_TOKEN_PREFIX = "ENTITY"

_SECRET_CACHE: dict[str, bytes] = {}

_default_manager = None


def token_prefix(pii_type: str) -> str:
    """Return the token prefix used for a PII type."""
    normalised_type = (
        (pii_type or "")
        .strip()
        .upper()
        .replace(" ", "_")
        .replace("-", "_")
    )

    if normalised_type in TOKEN_PREFIXES:
        return TOKEN_PREFIXES[normalised_type]

    cleaned_type = re.sub(
        r"[^A-Z0-9]",
        "",
        normalised_type,
    )

    if not cleaned_type or not cleaned_type[0].isalpha():
        return DEFAULT_TOKEN_PREFIX

    return cleaned_type[:16]


def create_token(pii_type: str) -> str:
    """Create a new random token for a PII type."""
    random_part = secrets.token_hex(
        TOKEN_HEX_CHARACTERS // 2
    ).upper()

    return f"{token_prefix(pii_type)}_{random_part}"


def is_valid_token(token: object) -> bool:
    """Return True when a value has the shape of a RoboPII token."""
    if not isinstance(token, str):
        return False

    return TOKEN_PATTERN.match(token) is not None


def normalise_value(pii_type: str, original_value: str) -> str:
    """Normalise a raw PII value so variants map to one token."""
    if original_value is None:
        raise ValueError("Original value cannot be None.")

    text = str(original_value).strip()

    if not text:
        raise ValueError("Original value cannot be empty.")

    prefix = token_prefix(pii_type)

    if prefix == "PHONE":
        digits = re.sub(r"\D", "", text)
        return digits or text.casefold()

    if prefix == "EMAIL":
        return text.casefold()

    return re.sub(r"\s+", " ", text).casefold()


def _secret_file_path() -> Path:
    """Return the location of the local vault secret file."""
    from robopii.storage import resolve_data_dir

    return resolve_data_dir() / SECRET_FILE_NAME


def _load_or_create_secret_file(path: Path) -> bytes:
    """Read the vault secret, creating it on first use."""
    if path.is_file():
        existing_secret = path.read_bytes().strip()

        if existing_secret:
            return existing_secret

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    new_secret = secrets.token_hex(32).encode("utf-8")

    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        # Another process created the secret first.
        return path.read_bytes().strip()

    with os.fdopen(descriptor, "wb") as secret_file:
        secret_file.write(new_secret)

    return new_secret


def secret_key() -> bytes:
    """Return the local secret used to derive lookup digests."""
    configured_secret = os.environ.get(SECRET_ENV_VAR)

    if configured_secret:
        return configured_secret.encode("utf-8")

    path = _secret_file_path()
    cached_secret = _SECRET_CACHE.get(str(path))

    if cached_secret is None:
        cached_secret = _load_or_create_secret_file(path)
        _SECRET_CACHE[str(path)] = cached_secret

    return cached_secret


def encryption_key() -> bytes:
    """Return the key used to encrypt identity values in the vault.

    Derived from the same local secret as the lookup digest, but
    through HKDF with a separate label, so the encryption key and the
    digest key are different values. Reusing one key for two purposes
    weakens both.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=ENCRYPTION_KEY_LABEL,
    ).derive(secret_key())

    # Fernet expects a urlsafe base64 encoded 32 byte key.
    return base64.urlsafe_b64encode(derived_key)


def lookup_hash(pii_type: str, original_value: str) -> str:
    """Return the keyed digest used to find a recurring entity."""
    message = (
        f"{token_prefix(pii_type)}:"
        f"{normalise_value(pii_type, original_value)}"
    )

    return hmac.new(
        secret_key(),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class TokenManager:
    """Create and reuse pseudonymised tokens for detected PII."""

    def __init__(
        self,
        vault=None,
        token_factory: Callable[[str], str] | None = None,
    ):
        self._vault = vault
        self.token_factory = token_factory or create_token

    @property
    def vault(self):
        """Return the protected vault backing this manager."""
        if self._vault is None:
            from robopii.storage import get_protected_vault

            self._vault = get_protected_vault()

        return self._vault

    def resolve_or_create_token(
        self,
        entity: DetectedPII,
    ) -> TokenMapping:
        """Return an existing token or create one for detected PII."""
        if entity is None:
            raise ValueError("Detected PII cannot be None.")

        # Raises when the value is empty.
        normalise_value(
            entity.pii_type,
            entity.original_value,
        )

        digest = lookup_hash(
            entity.pii_type,
            entity.original_value,
        )

        existing_mapping = self.vault.find_mapping_by_lookup_hash(
            digest
        )

        if existing_mapping is not None:
            return TokenMapping(
                token=existing_mapping.token,
                pii_type=existing_mapping.pii_type,
                original_value=entity.original_value,
            )

        new_mapping = TokenMapping(
            token=self.token_factory(entity.pii_type),
            pii_type=entity.pii_type,
            original_value=entity.original_value,
        )

        if not is_valid_token(new_mapping.token):
            raise ValueError(
                f"Generated token has an invalid shape: "
                f"{new_mapping.token!r}"
            )

        # The vault keeps the first token when two callers race.
        return self.vault.save_mapping(new_mapping)

    def token_for_value(
        self,
        pii_type: str,
        original_value: str,
    ) -> str | None:
        """Return the token already used for a value, if any."""
        mapping = self.vault.find_mapping_by_lookup_hash(
            lookup_hash(pii_type, original_value)
        )

        return None if mapping is None else mapping.token

    def resolve_token(
        self,
        token: str,
        actor: str = "unknown",
        reason: str = "unspecified",
    ) -> TokenMapping | None:
        """Return the protected identity behind a token.

        Every call is written to the vault access log.
        """
        return self.vault.get_mapping(
            token,
            actor=actor,
            reason=reason,
        )


def get_token_manager() -> TokenManager:
    """Return the shared token manager."""
    global _default_manager

    if _default_manager is None:
        _default_manager = TokenManager()

    return _default_manager


def reset_token_manager() -> None:
    """Drop the shared manager and cached secrets.

    Used by tests and after the storage location changes.
    """
    global _default_manager

    _default_manager = None
    _SECRET_CACHE.clear()


def resolve_or_create_token(entity: DetectedPII) -> TokenMapping:
    """Return an existing token or create one for detected PII."""
    return get_token_manager().resolve_or_create_token(entity)
