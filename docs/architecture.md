# RoboPII Integration Contract

## Processing Flow

1. The pipeline receives an image, video, audio file or transcript.
2. The visual scrubber detects and obscures faces.
3. The audio processor converts audio into a transcript.
4. The text scrubber detects and masks transcript PII.
5. The token manager creates or retrieves pseudonymised tokens.
6. Scrubbed records are saved in the primary store.
7. Sensitive identity mappings are saved in the protected vault.
8. Authorized retrieval uses a token to locate previous scrubbed context.

## Component Ownership

- Mir: visual scrubbing
- Moazam: conversational and text scrubbing
- Andre: tokens and separated storage
- Aiden: retrieval and application integration

## Integration Rules

- Components must use the shared classes in `models.py`.
- Agreed function names and parameters must not be changed without team approval.
- Raw PII must never be saved in the primary store.
- Database files and generated media must not be committed.
- Every component must include tests.
- All changes must be reviewed through pull requests.

## Storage Separation

### Primary Store

The primary store may contain:

- Record ID
- Pseudonymised tokens
- Scrubbed transcript
- Scrubbed media location
- Timestamp
- Non-sensitive metadata

### Protected Vault

The protected vault may contain:

- Token
- PII type
- Sensitive identity value or protected reference
- Creation timestamp

The primary store must not contain original names, phone numbers, email
addresses or other raw PII.