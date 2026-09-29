# RoboPII Architecture and Integration Contract

## Processing Flow

1. The pipeline receives an image, video, audio file or transcript.
2. The visual scrubber detects and obscures faces.
3. The audio processor converts audio into a transcript.
4. The text scrubber detects and masks transcript PII.
5. The token manager creates or retrieves pseudonymised tokens.
6. Scrubbed records are saved in the primary store.
7. Sensitive identity mappings are saved in the protected vault.
8. Authorized retrieval uses a token to locate previous scrubbed context.

```text
                         robopii/pipeline.py
 input file ─────────────────────────────────────────────────────────────┐
   │                                                                     │
   ├─ image ──> video_scrubber.scrub_image ──┐                           │
   ├─ video ──> video_scrubber.scrub_video ──┤ blurred media             │
   │      └───> audio_processor.transcribe_audio ─┐                      │
   ├─ audio ──> audio_processor.transcribe_audio ─┤ raw transcript       │
   └─ .txt ───────────────────────────────────────┤ (memory only)        │
                                                  v                      │
                                  text_scrubber.scrub_text               │
                                                  │ [PERSON_1] + DetectedPII
                                                  v                      │
                          token_manager.resolve_or_create_token ──> vault.db
                                                  │ PERSON_A1B2C3D4E5F6  (encrypted)
                                                  v                      │
                     placeholders -> tokens, leak check                  │
                                                  v                      │
                           storage.save_primary_record ──> primary.db ◄──┘
                                                        (no raw PII)

   robopii/retrieval.py
     retrieve_context / build_context / recall_entity   reads primary.db only
     resolve_identity / forget_identity                  vault, authorised + logged
```

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

## Component Interfaces Used by the Pipeline

| Step | Function | Returns |
| --- | --- | --- |
| Image | `video_scrubber.scrub_image(input_path, output_path)` | `VisualScrubResult` |
| Video | `video_scrubber.scrub_video(input_path, output_path, original_output_dir)` | `VisualScrubResult` |
| Audio | `audio_processor.transcribe_audio(input_path)` | `TranscriptResult` |
| Text | `text_scrubber.scrub_text(text)` | `TextScrubResult` |
| Tokens | `token_manager.resolve_or_create_token(entity)` | `TokenMapping` |
| Store | `storage.save_primary_record(record)` | record ID |
| Pipeline | `pipeline.process_video(input_path)` and `process_image`, `process_audio`, `process_transcript`, `process_file` | `ProcessingResult` |

`pipeline.PipelineComponents` holds these functions. The pipeline imports each
one only when it is first used, so a transcript can be processed without
MediaPipe or Whisper installed, and tests can swap any single component for a
fake without patching modules.

## Pipeline Behaviour

**Where raw PII exists.** Raw values exist only in memory while a record is
processed:

- The unscrubbed transcript is never written to disk.
- The audio processor writes its temporary WAV file to the system temp
  directory and deletes it.
- The video scrubber writes unblurred frames to `original_output_dir`. The
  pipeline points this at a new temporary directory and deletes it in a
  `finally` block, so the frames are removed even when processing fails.
  Setting `pipeline.keep_original_frames: true` keeps them in
  `data/original_frames/<record_id>/`, which is for debugging with synthetic
  data only.
- Detected values go to the vault through the token manager, encrypted.

**Output location.** Scrubbed media is written to
`<output_dir>/<record_id>/`, not to a folder named after the input file.
File names can contain PII (for example `jane_smith_0412345678.mp4`), and the
media path is stored in the primary database.

**Tokens in transcripts.** The text scrubber returns numbered placeholders
(`[PERSON_1]`). The pipeline replaces each one with the token it resolved to
(`[PERSON_A1B2C3D4E5F6]`). The same person therefore reads the same way
across every stored conversation, and a later retrieval can tell what that
person said. `pipeline.link_tokens_in_transcript: false` keeps the numbered
placeholders instead.

**Two checks before storage.**

1. *Leak check (pipeline).* Every value the text scrubber detected is looked
   for again in the final transcript, as a whole word. Values of four or
   more characters are matched ignoring case, which catches `jane smith`
   left behind after `Jane Smith` was scrubbed. Shorter values are matched
   exactly, so the location `US` does not flag the word `us`. A hit raises
   `RawPIIError`, and the error message names the PII type, never the value.
2. *Pattern guard (storage).* `validate_primary_record` rejects emails,
   phone numbers, ID numbers and long digit runs, which catches PII that the
   scrubber did not detect at all.

If either check fails, nothing is written to `primary.db` and the scrubbed
media folder for that record is deleted.

**Videos without audio.** If the audio processor raises `ValueError` (no audio
track), the record is stored with the blurred frames and a null transcript,
and `metadata.audio_status` is set to `no_audio`. If transcription is not
available on the machine, it is set to `transcriber_unavailable`.

**Metadata stored with each record** (non-sensitive): `input_type`,
`audio_status`, `faces_detected`, `pii_counts` by type, and
`timings_seconds` for each stage (`visual`, `transcription`, `text_scrub`,
`tokenisation`, `total_before_storage`). The timings are what the latency
evaluation reads.

## Retrieval Levels

| Function | Reads | Reveals identity | Logged in vault |
| --- | --- | --- | --- |
| `retrieve_context(token)` | primary.db | No | No |
| `build_context(token)` | primary.db | No | No |
| `recall_entity(type, value)` | vault lookup digest + primary.db | No | No |
| `resolve_identity(token, actor, reason)` | vault | Yes | Always (`lookup` or `denied`) |
| `forget_identity(token, actor, reason)` | vault | No | Always (`delete` or `denied`) |

- `retrieve_context` and `build_context` are what the robot uses to continue
  a conversation. They return de-identified history only.
- `recall_entity` handles "have I met this person before?" when a name,
  email or phone number is heard again. It goes through the keyed HMAC digest,
  so no plaintext is searched and no new token is created.
- `resolve_identity` and `forget_identity` require the actor to be listed in
  `retrieval.authorised_actors` and a reason of at least
  `retrieval.min_reason_length` characters. Refused attempts are written to
  the vault access log with action `denied`.
- Every retrieval function rejects anything that is not a well-formed token,
  so a raw name can never be used as a lookup key.

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
addresses or other raw PII. `pipeline.audit_primary_store()` (and
`python main.py audit`) checks this for a whole database: it validates every
record, then decrypts each vault value and looks for it in every record and
in the raw bytes of `primary.db`. Each decryption is logged under the actor
`audit`.

See `docs/storage_design.md` for the database schema and encryption.

## Configuration

Shared settings live in `config/settings.yaml` and are loaded through
`robopii.config.get_config()`. Environment variables owned by a component
(for example `ROBOPII_DATA_DIR`, `ROBOPII_VAULT_SECRET`) take priority over the
file. `ROBOPII_CONFIG` selects a different settings file. Unknown keys and
wrong types are rejected when the file is loaded.

## Known Limitations

- **Faces are blurred but not yet tokenised.** `face_recognizer.py` can match
  a face across frames, but `scrub_video` does not return face crops or boxes,
  so the pipeline cannot pass faces to it. Linking a face to a `PERSON_` token
  needs `VisualScrubResult` (or a new function) to expose the detected
  regions, which is an interface change for the team to agree.
- **Recall depends on spelling.** A returning person is recognised only if
  the name is transcribed the same way (ignoring case and spacing).
- **Actors are self-declared.** The authorisation check compares a name
  against a list. There are no user accounts or passwords, which is
  acceptable for a single-operator prototype but not for deployment.
- **Model settings are not configurable yet.** The Whisper model size and
  face detection confidence are hard-coded in their own modules. They can be
  moved into `settings.yaml` when their owners are ready.
