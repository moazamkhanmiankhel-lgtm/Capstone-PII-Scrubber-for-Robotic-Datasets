# RoboPII handover

## Delivered scope

Version 0.1.0 includes an integrated video pipeline, face detection and blur,
face-template matching, Whisper transcription, text PII detection, token reuse,
primary SQLite storage, an encrypted identity vault, retrieval and audit commands.
The repository supplies source, tests, configuration, model assets and build
metadata. The GitHub Release supplies the installable wheel.

Start with [How to use](how_to_use.md).

## Final acceptance on Windows

Run from the updated repository using the Python environment with all
dependencies and `en_core_web_md` installed:

```powershell
ffmpeg -version
py -m pytest -q
```

Video and audio were evaluated separately; speaking video is outside the required
scope. See [evaluation.md](evaluation.md) for methods and
[evaluation_results.md](evaluation_results.md) for measured results and report text.

## Acceptance evidence (23 October 2026)

Evidence below was supplied by the developer from the Windows source checkout;
it was not independently rerun on that machine by the reviewer.

- Windows suite: **180 passed, 2 skipped, 1 warning in 31.12 seconds**.
  The skips require macOS `say` and POSIX permission semantics respectively.
  The warning is Whisper falling back to FP32 on CPU.
- Manual vid1 output inspection: developer reported all visible faces blurred.
- Labelled face detection: 14 true positives, 0 false positives and 0 false
  negatives across 14 sampled frames in two videos of the same participant.
  Precision 100%, recall 100%, F1 1.00 at IoU >= 0.50.
- Vid2 reused vid1's face token: one successful same-person cross-clip case.
- Synthetic audio: raw WER 42.1% (16 edits / 38 reference words). Text detection
  on the scripted reference found 3 of 4 entities: precision 100%, recall 75%,
  F1 0.857. A phone number expressed as words was missed.
- Direct audio-file processing stored an `audio` record with all four detected
  sensitive spans tokenised. Transcription took 4.7844 seconds; total before
  storage was 19.8575 seconds.
- Latest primary-store audit passed across 9 records and checked 9 identities.
  This checks patterns/known mappings, not every possible privacy leak.

## Deferred checks

The team elected not to perform these checks for this handover:

- Different-person recognition: false identity matches are unmeasured.
- Fresh Windows installation and end-to-end use of the published Release wheel.

An earlier review of source `ee36a64` built and separately installed a wheel,
checked CLI startup outside the source tree and confirmed bundled face assets.
That is distinct from acceptance of the exact published wheel on Windows.
These deferred checks do not block this scoped research-prototype handover;
they remain necessary evidence before making broader deployment claims.

## Limitations and operation

- This is a research prototype. Detection and recognition can miss or
  misclassify inputs; manual accuracy evaluation remains necessary.
- MP4 exports have no audio. Audio is delivered as scrubbed text.
- Face and name identities are separate; co-occurrence is not speaker identity.
- CLI actor names implement a demonstration allowlist, not authentication.
- Primary and vault writes are not a single transaction. Failures may leave
  vault entries without a corresponding primary record.
- Back up the databases and required vault key/secret together. Preserve
  output media alongside database references. Do not share secrets with source.
- Keep `keep_original_frames: false` for normal operation.
- Review model redistribution licences before broader distribution.

## Maintainer tasks

Build from the intended release commit with `py -m build`; attach the wheel
to the matching GitHub Release. `dist/` is intentionally ignored by Git.
For future releases, test the exact published wheel in a new environment.
Keep the source commit and wheel checksum with each release. The current
Windows published-wheel acceptance check is explicitly deferred above.
