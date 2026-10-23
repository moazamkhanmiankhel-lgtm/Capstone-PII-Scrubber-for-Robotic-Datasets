# Repository review and Python package handover

Historical review of commit `926cb3c` before packaging was merged.
Packaging was subsequently merged into main at `ee36a64`.
The verification results below describe that earlier review, not a new test run.
See [handover.md](handover.md) for current acceptance status.

## Verdict

The central pipeline is implemented and connected to SQLite. Packaging was
missing and is supplied here. This is a functioning research prototype;
final acceptance still requires a representative face-and-speech clip on
the target Windows machine. Tests cannot certify detection accuracy.

## Database connections

| Input / feature | Route and persistence |
| --- | --- |
| Video | Face blur -> face templates in encrypted vault -> silent MP4 path and face tokens in primary record |
| Video speech | Whisper -> text scrubber -> encrypted PII mappings in vault; scrubbed transcript and tokens in the same primary record as video |
| Audio file | Whisper -> text scrubber -> scrubbed transcript in primary; mappings in vault; no rewritten audio file |
| Text | spaCy + regex -> tokenised transcript in primary; encrypted mappings in vault |
| Image | Blurred image path and detection count in primary; image route does not currently create face identity tokens |
| Recall/context | Finds past primary records through shared tokens |
| Reveal/forget | Reads/deletes vault mapping and records access attempts |

`Pipeline._store()` is the common primary write point. Text mapping writes
come through `resolve_or_create_token()`. Video loads saved encrypted face
templates and uses the same token manager for new faces. The two SQLite
files are independent; this is application-level linkage, not cross-file
foreign keys or an atomic transaction across both databases.

## Changes supplied

- Added pyproject.toml, dependency extras, wheel/source builds and `robopii`
  console command; `python -m robopii` is also supported.
- Kept `python main.py` as a compatibility wrapper.
- Bundled the existing BlazeFace and SFace models inside the installed package.
- Moved writable defaults from the installed-code directory to the working
  directory. Existing source-checkout usage from the repository root is retained.
- Routed the legacy video-only command through the central pipeline. It no
  longer retains raw frames by default or stores the input filename as a label.
- Added a regression assertion for that raw-frame leak.
- Skipped the macOS-only `say` test explicitly when that program is absent.
- Updated installation and handover instructions.

## Verification performed

- Full suite: **181 passed, 1 skipped**, Python 3.12 on Linux.
- The skip is the speech test requiring macOS `say`.
- Real-component tests ran with MediaPipe, OpenCV, spaCy en_core_web_md,
  Whisper base and ffmpeg. The real video fixture contains generated blank
  frames, and the real audio fixture is silence; neither proves face/speech
  accuracy on a real recording. Face-token persistence is also exercised
  with controlled fake detections/embeddings.
- Built wheel and source distribution with `python -m build --no-isolation`.
- Installed wheel into a separate directory and imported it outside the
  repository. Real text processing produced primary and vault records,
  recall returned the stored interaction and the primary-store audit passed.
- Verified both face-model assets are present in the installed wheel.
- This Linux runner needed libGLESv2 for MediaPipe; a temporary extracted
  system library was used for the test run. It is not bundled in the package.

## Remaining scope and limits

- Silent MP4 export is intentional and accepted. Audio is represented by its
  scrubbed transcript, not a censored/re-encoded audio file.
- A face token and a name token are separate. They share a record; the system
  does not establish that a face is the named speaker.
- The image route stores blurred media but does not create face identity tokens.
- The actor allowlist is a demo policy: callers supply actor names themselves;
  it is not user authentication. Direct low-level vault APIs can bypass it.
- Detection can miss names/faces. The audit checks patterns and known values;
  it does not prove that every possible PII value was removed.
- Vault mappings can remain if a later pipeline stage fails. Primary writes
  and vault writes do not form one transaction.
- YAML storage configuration is applied by the CLI. Python callers using
  custom settings should call `set_config(settings)` and
  `apply_storage_settings(settings)` before processing; storage is shared state.
- Verify model redistribution licences before publishing outside the capstone.
  This handover does not add a project licence or publish anything to PyPI.

## Windows final acceptance

From the updated project directory:

```powershell
py -m pip install ".[all,dev]"
py -m spacy download en_core_web_md
ffmpeg -version
py -m pytest
py -m robopii process "sample_data\videos\vid1.mp4"
py -m robopii records
py -m robopii audit
```

Confirm `audio_status` is `transcribed` for a clip with speech, the stored
transcript hides spoken PII, and the MP4 visibly blurs all faces. Process a
second clip of the same face and confirm token reuse. Record accuracy and
latency limitations honestly. Run from the same working directory each time
or set ROBOPII_DATA_DIR to the existing data directory.

Build distributables with `py -m build`. A wheel can be handed over directly;
PyPI publication is not required to deliver a Python package.
