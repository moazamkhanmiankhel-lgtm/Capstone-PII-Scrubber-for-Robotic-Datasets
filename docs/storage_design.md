# RoboPII Storage Design

This document describes the token generation and separated storage layer
(`robopii/token_manager.py` and `robopii/storage.py`). It covers the two
databases, the token scheme, vault encryption, the guard that keeps raw
PII out of the primary store, the public functions and the known
limitations.

Two requirement numbering schemes are in use on this project, and they
collide: the sponsor brief numbers requirements FR-01 to FR-06, while
Section 3 of the team report numbers them FR-1 to FR-12 with separate
TR and NFR lists. The same label means different things in each, so
this document names both.

| Sponsor brief | Team report | Covered here |
| --- | --- | --- |
| FR-03 Pseudonymised linking | FR-4, FR-5 | Sections 2, 2a |
| FR-04 Separated storage | FR-8, FR-9 | Sections 1, 3 |
| FR-05 Basic context retrieval | FR-10 | Section 5 |
| FR-06 Configurability | FR-12, NFR-7 | Section 6 |
| (no equivalent) | FR-11 Removal of a mapping | Section 5 |
| (no equivalent) | TR-4, TR-5, TR-6 | Sections 1, 2, 2a |

The sponsor brief also asks for "an encrypted or tokenised secondary
database": both are implemented, the primary store being tokenised and
the vault encrypted.


## 1. Two separate databases

```text
                 [ pipeline ]
                      |
      +---------------+-----------------+
      |                                 |
      v                                 v
 data/primary.db                    data/vault.db
 scrubbed records                   token -> identity
 + pseudonymised tokens             + access log
 (no raw PII)                       (encrypted, owner only)
```

The two stores are **separate SQLite files**, not two tables in one
file. A single file would mean that anybody who copies the robot memory
also copies every identity mapping. With two files the primary database
can be backed up, shared with the team or inspected during a demo while
the vault stays behind.

The only thing the two stores have in common is the token. The primary
store cannot resolve a token on its own, and the vault holds no
interaction history.

Both files are created with mode `0600` (owner read and write only).
Neither file is committed: `*.db` and `data/vault.key` are in
`.gitignore`.


## 2. Token scheme

A token is the pseudonym that stands in for a person or another PII
value:

```text
PERSON_A1B2C3D4E5F6
^      ^
|      12 random hex characters (48 bits from secrets.token_hex)
PII type prefix
```

* The random part carries no information about the person. It is not
  derived from the value, so a token cannot be reversed or guessed back
  into an identity.
* The prefix only says *what kind* of thing the token replaces, which
  keeps records readable during a demo.
* Types that mean the same thing share a prefix, so the text scrubber
  and the face recognizer stay interoperable:
  `NAME`, `PERSON_NAME`, `FACE` and `SPEAKER` all become `PERSON_...`,
  `EMAIL_ADDRESS` becomes `EMAIL_...`. An unknown type falls back to
  its own cleaned-up prefix, or to `ENTITY_...`.
* `is_valid_token()` checks this shape. The primary store refuses any
  record whose `tokens` list contains something that is not a token,
  which stops a raw name being written into the link table by mistake.

### Reusing a token for a recurring entity

A recurring person must get the same token again, otherwise the robot
loses all long-term context. Looking the value up in plaintext would
mean keeping a searchable index of raw PII, so the vault stores a
**keyed digest** instead:

```text
lookup_hash = HMAC-SHA256(local secret, "<PREFIX>:<normalised value>")
```

1. The value is normalised first, so formatting differences do not
   create a second token: emails are lower-cased, phone numbers are
   reduced to their digits, names are lower-cased with collapsed
   whitespace.
2. `identity_mappings.lookup_hash` is `UNIQUE`, so one identity can only
   ever have one token.
3. `TokenManager.resolve_or_create_token()` computes the digest, returns
   the existing token if one is found, and otherwise creates a new token
   and saves the mapping.

HMAC is used rather than a plain hash so the digest cannot be attacked
with a dictionary of common names or phone numbers: without the local
secret an attacker who steals `vault.db` cannot test guesses against
the digest column.

The secret comes from the `ROBOPII_VAULT_SECRET` environment variable.
If it is not set, a 32-byte random secret is created once in
`data/vault.key` (mode `0600`).


## 2a. Vault encryption

`identity_mappings.encrypted_value` holds the identity value encrypted
with Fernet (AES-128-CBC with an HMAC-SHA256 authentication tag), from
the `cryptography` package. Values are encrypted on write and decrypted
only when an authorised lookup asks for them, so the plaintext is never
at rest on disk.

The encryption key is **derived** from the same local secret as the
lookup digest, using HKDF-SHA256 with a separate label
(`robopii-vault-encryption`). One secret is convenient to manage, but
using one key for two purposes weakens both, so the two keys are
different values derived from it.

**The secret is now the only thing standing between the vault file and
the identities in it. Losing it makes the vault permanently
unreadable**, not merely unlinkable: identities cannot be decrypted and
existing digests cannot be recomputed. Back up `data/vault.key`
wherever the vault itself is backed up, and never commit it.

A vault created before encryption was added is refused at startup with
an explanatory error rather than being read or silently migrated, since
its plaintext cannot be re-encrypted without re-reading every value.


## 3. Database schema

### `primary.db`

`records` — one row per processed interaction.

| Column | Type | Notes |
| --- | --- | --- |
| `record_id` | TEXT | Primary key, a UUID4 by default |
| `created_at` | TEXT | ISO-8601 UTC, millisecond precision |
| `scrubbed_media_path` | TEXT | Path to blurred frames or video |
| `scrubbed_transcript` | TEXT | Masked transcript |
| `source_label` | TEXT | Optional non-sensitive label |
| `metadata` | TEXT | JSON object, non-sensitive metadata |

`record_tokens` — links records to pseudonyms (many-to-many).

| Column | Type | Notes |
| --- | --- | --- |
| `record_id` | TEXT | References `records`, cascade delete |
| `token` | TEXT | Pseudonymised token |
| `position` | INTEGER | Order the tokens appeared in |

Primary key `(record_id, token)`, so a token repeated inside one record
is stored once. Indexed on `token`, which is the column
`find_records_by_token()` searches.

### `vault.db`

`identity_mappings` — the protected mapping.

| Column | Type | Notes |
| --- | --- | --- |
| `token` | TEXT | Primary key |
| `pii_type` | TEXT | Type reported by the detector |
| `encrypted_value` | BLOB | The identity value, encrypted (section 2a) |
| `lookup_hash` | TEXT | `UNIQUE`, HMAC digest (see section 2) |
| `created_at` | TEXT | First time the identity was seen |
| `last_seen_at` | TEXT | Most recent time it was saved |

`access_log` — every attempt to resolve or remove a token.

| Column | Type | Notes |
| --- | --- | --- |
| `access_id` | INTEGER | Autoincrement primary key |
| `token` | TEXT | Token that was looked up |
| `accessed_at` | TEXT | ISO-8601 UTC |
| `actor` | TEXT | Who asked |
| `reason` | TEXT | Why they asked |
| `action` | TEXT | `lookup` or `delete` |
| `was_found` | INTEGER | 1 when the token existed |

Both databases carry a `schema_version` table (currently version 2) so a
later change can be migrated instead of guessed at.


## 4. Keeping raw PII out of the primary store

`validate_primary_record()` runs before every primary insert and rejects
the record instead of storing it:

1. **Field whitelist.** Only the seven fields in the table above are
   accepted. A stray `speaker_name` field raises `ValueError` rather
   than being silently dropped.
2. **Token shape.** Every entry in `tokens` must match the token
   pattern, so a raw value cannot be smuggled in as a link.
3. **Raw PII patterns.** `scrubbed_media_path`, `scrubbed_transcript`,
   `source_label` and all strings inside `metadata` are scanned for
   email addresses, phone numbers, government ID numbers and long digit
   sequences. A match raises `RawPIIError` and nothing is written.

Number patterns only count when the match contains enough digits (nine
for a phone number, ten for a bare digit run), so ordinary content such
as `2026-05-01`, `10:30` or `order 12345678` is still accepted.

This is a **backstop, not a detector**. It catches the mistakes that
matter most in a prototype — a transcript that was never scrubbed, a
filename that still contains a phone number — but it cannot recognise a
plain name, so it is not a substitute for the text scrubber. The two
false-negative cases worth knowing about are digits joined to a word by
an underscore (`file_5551234567`, deliberately allowed so frame names
like `clip_000123` keep working) and names of any kind.


## 5. Public API

```python
from robopii.storage import (
    initialise_databases,     # create both databases
    save_primary_record,      # dict -> record_id
    save_protected_mapping,   # dict or TokenMapping -> TokenMapping
    find_records_by_token,    # token -> list of scrubbed records
    get_primary_record,       # record_id -> record or None
    get_protected_mapping,    # token -> TokenMapping or None (logged)
    delete_protected_mapping, # forget one identity (logged)
    configure_storage,        # change where the databases live
    close_databases,
)
from robopii.token_manager import resolve_or_create_token
```

The four function names agreed in `docs/architecture.md` are unchanged.
`save_protected_mapping()` now returns the stored `TokenMapping` instead
of `None`, which callers may ignore.

`PrimaryStore` and `ProtectedVault` are also available as classes when a
caller needs an explicit database path, which is how the tests avoid
touching `data/`.

### Typical pipeline use

```python
initialise_databases()

tokens = []
for entity in text_result.detected_pii:
    mapping = resolve_or_create_token(entity)   # reuses known people
    tokens.append(mapping.token)
    save_protected_mapping({
        "token": mapping.token,
        "pii_type": mapping.pii_type,
        "original_value": mapping.original_value,
    })

record_id = save_primary_record({
    "scrubbed_media_path": visual_result.output_path,
    "scrubbed_transcript": text_result.scrubbed_text,
    "tokens": tokens,
})
```

Both writes are idempotent for a known identity: saving the same
mapping again keeps the first token and only updates `last_seen_at`, so
the extra `save_protected_mapping()` call above is harmless.

### Controlled retrieval

```python
history = find_records_by_token(token)        # de-identified context
identity = get_protected_mapping(             # crosses into the vault
    token,
    actor="operator",
    reason="returning visitor",
)
```

Reading interaction history never touches the vault. Only
`get_protected_mapping()` / `TokenManager.resolve_token()` open the
protected store, and every such call is written to `access_log` with the
actor and the reason, whether or not the token was found. The retrieval
package (Package 4) builds its authorisation check on top of this.

### Forgetting a person

```python
delete_protected_mapping(
    token,
    actor="operator",
    reason="withdrawal of consent",
)
```

Deleting the mapping leaves the scrubbed records in the primary store
and makes them **permanently unlinkable**: the token stays on the
records, but nothing connects it to a person any more, and no future
encounter can resolve to it because the lookup digest is gone with the
mapping. The deletion is written to `access_log` with `action` set to
`delete`. This is the deletion path report FR-11 asks for, and the
benefit the background study argues for in Section 2.6 of the report.


## 6. Configuration

| Setting | Environment variable | Default |
| --- | --- | --- |
| Data directory | `ROBOPII_DATA_DIR` | `<project>/data` |
| Primary database | `ROBOPII_PRIMARY_DB` | `<data dir>/primary.db` |
| Vault database | `ROBOPII_VAULT_DB` | `<data dir>/vault.db` |
| Vault secret | `ROBOPII_VAULT_SECRET` | `<data dir>/vault.key` |

`configure_storage(data_dir=..., primary_db_path=..., vault_db_path=...)`
overrides the same settings from Python and takes priority over the
environment. Calling `configure_storage()` with no arguments clears the
overrides. Both forms close any open database and reset the shared token
manager, so the next call reopens at the new location.


## 7. Tests

`tests/test_token_manager.py` (24 tests) covers the token shape, prefix
grouping, value normalisation, digest stability and secrecy, derivation
of a separate encryption key, token reuse for recurring entities across
manager instances, rejection of empty values and of factories that
return something that is not a token, and the access-logged resolve
path.

`tests/test_storage.py` (38 tests) covers schema creation, the
separation of the two files, record round trips, token linking and
ordering, every branch of the raw-PII guard including the
false-positive cases that must still be accepted, duplicate record IDs,
vault idempotency, refusal to reuse a token for a second identity,
encryption at rest, refusal to read the vault under a different secret,
refusal to open a pre-encryption vault, deletion and its access log
entry, and configuration overrides.

One test is worth pointing at during the demo:
`test_raw_identity_is_unreadable_in_both_databases` writes a full
record, closes both databases and then greps the raw bytes of each file
— the email address is absent from `primary.db` by design and
unreadable in `vault.db` because it is encrypted, yet an authorised
lookup still returns it.

```bash
python -m pytest tests/test_storage.py tests/test_token_manager.py -v
```


## 8. Limitations and future work

* **The secret sits next to the vault by default.** Identity values are
  encrypted, but the key that decrypts them lives in `data/vault.key`
  in the same directory unless `ROBOPII_VAULT_SECRET` supplies it from
  elsewhere. Anyone who takes both files takes the identities. On a real
  deployment the secret belongs in an OS keychain or a hardware-backed
  store; for the prototype, keeping it out of backups and out of version
  control is the practical measure.
* **There is no key rotation.** Rotating the secret orphans every
  existing digest and makes the stored values undecryptable. Re-keying
  would mean decrypting and re-encrypting the whole vault under the old
  key first, which is not implemented.
* **Phone normalisation is literal.** `+1 555 123 4567` and
  `555-123-4567` produce different digests, so the same person can end
  up with two tokens if the transcript uses different formats.
* **The raw-PII guard is pattern based** and cannot see names (see
  section 4).
* **Deletion removes the mapping, not the records.** This is deliberate
  — report FR-11 asks that the scrubbed records become unlinkable, not that
  they disappear — but a caller wanting the records gone as well has to
  delete them separately, and no such call exists yet.
* **Anyone who can call the code can call deletion.** The access log
  records who deleted what and why, but the actor is self-declared and
  nothing enforces authorisation; that policy belongs to the retrieval
  package.
* **Single process assumption.** SQLite handles concurrent writers
  poorly under load; the prototype opens one connection per store and
  relies on the `UNIQUE` constraint to settle races between processes.
