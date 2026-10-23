# RoboPII Scrubber

A context-preserving PII scrubbing prototype for robotic datasets.

Robots that work around people record faces and conversations. RoboPII removes
personally identifiable information before anything is stored, but keeps a
protected, encrypted link from each person to a pseudonymised token. The robot
can then recognise that it has met someone before, and see what happened last
time, without its memory holding their name, face or phone number.

## Components

- Video face detection and obfuscation
- Audio transcription
- Text PII detection and redaction
- Pseudonymised token generation
- Separated primary and protected storage
- Controlled context retrieval

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate         # macOS / Linux

pip install ".[all,dev]"
python -m spacy download en_core_web_md
```

Audio transcription also needs **ffmpeg** on the PATH (`winget install ffmpeg`,
`brew install ffmpeg` or `apt install ffmpeg`). Whisper downloads its model
the first time it runs.

## Usage

```bash
# Scrub and store an input (image, video, audio or .txt transcript)
python -m robopii process sample_data/videos/clip.mp4
python -m robopii process --text "Hi, I'm Jane Smith, call me on 0412 345 678"

# Browse what is stored (no identities shown)
python -m robopii records
python -m robopii context PERSON_A1B2C3D4E5F6

# Has the robot met this person before?
python -m robopii recall --type PERSON --value "Jane Smith"

# Reveal or delete an identity (authorised actors only, always logged)
python -m robopii reveal PERSON_A1B2C3D4E5F6 --actor operator --reason "returning visitor"
python -m robopii forget PERSON_A1B2C3D4E5F6 --actor operator --reason "consent withdrawn"
python -m robopii access-log

# Check the primary store holds no raw PII
python -m robopii audit
```

Example output:

```text
$ python -m robopii process --text "Hi, I'm Jane Smith from Sydney, call me on 0412 345 678"
Tokens:           PERSON_31F77AC9E5CD, LOCATION_1694D6C469F6, PHONE_1CABAA4FCC53
Scrubbed transcript:
  Hi, I'm [PERSON_31F77AC9E5CD] from [LOCATION_1694D6C469F6], call me on [PHONE_1CABAA4FCC53]
```

From Python:

```python
from robopii.pipeline import process_file
from robopii.retrieval import build_context, recall_entity

result = process_file("clip.mp4")          # ProcessingResult
token, history = recall_entity("PERSON", "Jane Smith")
summary = build_context(token)             # de-identified history
```

## Configuration

Settings are in `config/settings.yaml`: output folders, whether to keep
unblurred frames (off), whether to transcribe video audio, who may reveal
identities, and how much history retrieval returns. Environment variables
override the file:

| Variable | Purpose |
| --- | --- |
| `ROBOPII_CONFIG` | Use a different settings file |
| `ROBOPII_DATA_DIR` | Where `primary.db`, `vault.db` and `vault.key` live |
| `ROBOPII_VAULT_SECRET` | Vault secret, instead of `data/vault.key` |

## Project Layout

```text
robopii/
  models.py           shared data classes
  video_scrubber.py   face blurring (images and video frames)
  face_recognizer.py  same-person recognition
  audio_processor.py  Whisper transcription
  text_scrubber.py    regex + spaCy PII detection
  token_manager.py    pseudonymised tokens
  storage.py          primary store and encrypted vault
  retrieval.py        context retrieval and authorised identity access
  pipeline.py         connects everything; raw PII audit
  config.py           settings loader
main.py               command-line demo
config/settings.yaml  shared settings
docs/                 architecture, storage design, testing
tests/                unit, integration and end-to-end tests
```

## Documentation

- [How to use: install the wheel and process video](docs/how_to_use.md)
- [Handover and final acceptance checks](docs/handover.md)
- [Labelled accuracy evaluation](docs/evaluation.md)
- [Measured results and report summary](docs/evaluation_results.md)
- `docs/architecture.md`: processing flow, interfaces, retrieval levels
- `docs/storage_design.md`: databases, tokens, encryption
- `docs/testing.md`: how to test, what is covered, demo checklist
- `docs/developer_guide.md`: shared rules for contributors

## Testing

```bash
pytest
```

See `docs/testing.md`.

## Privacy Notes

- Never commit `data/`, `output/`, `.db` files, `vault.key` or recordings of
  real people. `.gitignore` covers these.
- Losing `data/vault.key` makes the vault permanently unreadable. Back it up
  together with `vault.db`.

## Build and install the Python package

```powershell
py -m pip install build
py -m build
py -m pip install ".\dist\robopii_scrubber-0.1.0-py3-none-any.whl[all]"
py -m spacy download en_core_web_md
robopii --help
```

The wheel bundles the BlazeFace and SFace model files. Whisper and the spaCy
language model are separate downloads; audio needs ffmpeg on PATH. For a
smaller installation select `[video]`, `[text]` or `[audio,text]` instead of
`[all]`. The base install provides storage, retrieval and configuration.

Run from a writable working directory: default `data/` and `output/scrubbed/`
folders are created there, outside the installed package. Relative YAML paths
also use that working directory. Use `ROBOPII_DATA_DIR` or `--config` to
choose different locations. Existing `python main.py` commands still work
from a source checkout. Exported MP4 files intentionally have no audio;
the scrubbed transcript is stored in the same database record.

For a source installation on Windows:

```powershell
py -m pip install ".[all,dev]"
py -m spacy download en_core_web_md
py -m robopii process "sample_data\videos\vid1.mp4"
py -m robopii records
py -m robopii audit
```

This is a research prototype. Face recognition and PII detection can miss or
misclassify inputs. Face and name tokens are independent, linked by record
co-occurrence, not verified speaker identity. CLI actor names are a demo access
policy, not authenticated users. See `docs/repository_review.md` for validation.
