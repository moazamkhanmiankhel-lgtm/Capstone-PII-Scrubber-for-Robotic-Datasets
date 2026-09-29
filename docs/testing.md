# RoboPII Testing

## Running the Tests

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_md   # text scrubber model
pytest                                    # everything
pytest tests/test_pipeline.py -v          # one file
pytest -k "EndToEnd" -v                   # one group
```

Tests never touch `data/` or `output/`. Each test gets its own temporary
directory for both databases and the scrubbed media, and a fixed vault secret.

## Test Layout

| File | Owner | What it covers |
| --- | --- | --- |
| `test_video_scrubber.py` | Package 1 | Face detection and blurring |
| `test_face_recognizer.py` | Package 1 | Same-person recognition |
| `test_audio_processor.py` | Package 2 | Whisper transcription |
| `test_text_scrubber.py` | Package 2 | Regex and NER detection |
| `test_token_manager.py` | Package 3 | Token shape, reuse, digests |
| `test_storage.py` | Package 3 | Schema, raw PII guard, encryption |
| `test_config.py` | Package 4 | Settings loading and validation |
| `test_retrieval.py` | Package 4 | Context retrieval, recall, authorisation |
| `test_pipeline.py` | Package 4 | Integration and end-to-end |

`tests/conftest.py` holds the shared fixtures, and `tests/fakes.py` holds the
fake components.

## Unit, Integration and End-to-End Tests

**Unit tests** check one function on its own, for example placeholder
linking, the leak check, input type detection and authorisation rules.

**Integration tests** run the real pipeline with the real token manager,
primary store and vault, but replace the model-based components with fakes:

| Fake | Replaces | Behaviour |
| --- | --- | --- |
| `FakeTextScrubber` | spaCy text scrubber | Detects a fixed list of names plus emails and phone numbers, and numbers placeholders the same way as the real scrubber |
| `FakeVideoScrubber` | MediaPipe video scrubber | Writes one scrubbed and one original frame; can simulate a crash |
| `FakeImageScrubber` | MediaPipe image scrubber | Writes one blurred image |
| `FakeTranscriber` | Whisper | Returns a fixed transcript, or raises as if the video had no audio |

The fakes make the integration tests fast (about 2 seconds for the whole
suite) and deterministic, and they let the tests produce failures that are
hard to trigger with real models, such as a scrubber that misses a name.

**End-to-end test** (`TestEndToEnd.test_meet_store_recall_reveal_forget`)
follows the story used in the demo:

1. The robot meets Jane on video. The frames are blurred and her name is
   tokenised.
2. The next day she says her name again. `recall_entity` finds her earlier
   record through her token.
3. An operator reveals her identity, and the lookup is logged.
4. She withdraws consent and `forget_identity` deletes her mapping. The
   records stay but can no longer be linked to her.
5. `audit_primary_store` confirms there is no raw PII in the primary store.

**Real-component tests** (`TestRealComponents`) run the pipeline with the
real video scrubber on a generated video, and with the real spaCy text
scrubber. Each one is skipped automatically when its model is not installed.

## Raw PII Checks

These tests check that the primary store never holds raw PII:

| Test | Check |
| --- | --- |
| `test_primary_database_file_contains_no_raw_values` | Reads the raw bytes of `primary.db` and searches for every original value |
| `test_vault_holds_values_encrypted` | Values are not readable in the bytes of `vault.db`, but an authorised lookup returns them |
| `test_leftover_name_stops_the_record` | A scrubber that misses a repeated name causes the record to be refused, and nothing is stored |
| `test_undetected_phone_number_is_blocked_by_storage` | The storage guard catches PII the scrubber did not detect at all |
| `test_video_with_audio` | The media path does not contain the input file name (`jane_smith_0412345678.mp4`) |
| `test_original_frames_are_deleted` | Unblurred frames are removed after processing |
| `test_failure_cleans_up_frames_and_stores_nothing` | A crash leaves no frames and no record behind |
| `TestAudit` | The audit catches a raw name written by a bug elsewhere, and does not print the name |

The same audit can be run against a real database:

```bash
python main.py audit
```

## Final Demonstration Checklist

To be run together with all component owners before the demo:

- [ ] `pytest` passes on every team member's machine.
- [ ] `python main.py process <video with speech>` produces blurred frames,
      a token-linked transcript and `audio_status: transcribed`.
- [ ] `python main.py process <image with faces>` reports `faces_detected` > 0.
- [ ] A second clip of the same speaker reuses the same `PERSON_` token.
- [ ] `python main.py recall --type PERSON --value "<name>"` finds both clips.
- [ ] `python main.py reveal <token> --actor intruder --reason test` is
      refused and shows as `denied` in `python main.py access-log`.
- [ ] `python main.py audit` reports PASS.
- [ ] `data/original_frames/` does not exist or is empty.
- [ ] `git status` shows no `.db`, `vault.key` or media files staged.

## Latency Measurement

Every stored record carries `metadata.timings_seconds` with the time spent in
each stage. `python main.py records` prints these. For the performance
evaluation, process the same set of sample clips several times and collect
the timings from the primary store rather than timing by hand.
