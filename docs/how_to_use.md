# How to use RoboPII

RoboPII is a research prototype for processing video: blur faces, transcribe
speech, replace detected text PII with tokens, and store de-identified records
in SQLite with identity mappings in an encrypted vault. Exported MP4s are silent;
speech is represented by the scrubbed transcript in the database.

## Install the release (Windows PowerShell)

Use Python 3.10 or newer. Download the wheel from
[Releases](https://github.com/moazamkhanmiankhel-lgtm/Capstone-PII-Scrubber-for-Robotic-Datasets/releases/tag/v0.1.0).
Create a working folder, copy the wheel into it, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ".\robopii_scrubber-0.1.0-py3-none-any.whl[all]"
python -m spacy download en_core_web_md
ffmpeg -version
python -m robopii --help
```

Install ffmpeg separately and ensure `ffmpeg -version` works before processing.
Whisper downloads its base model on first use, so allow internet access and
extra startup time. If PowerShell blocks activation, use
`.\.venv\Scripts\python.exe` in place of `python` in these commands.

## Process video

Run from the same writable working folder each time:

```powershell
python -m robopii process "C:\path\to\video.mp4"
python -m robopii records --limit 5
python -m robopii audit
```

The process command prints the record ID, output path, tokens and scrubbed
transcript. Open `output\scrubbed\<record-id>\scrubbed.mp4` and inspect the
whole clip for missed faces. A successful command does not prove accuracy.

Default runtime files are `data\primary.db`, `data\vault.db`,
`data\vault.key` and `output\scrubbed\`. These are created outside the installed
package. Preserve the vault key with the vault database; losing it prevents
decryption. If using `ROBOPII_VAULT_SECRET`, preserve that secret instead.
Do not commit runtime files or real-person recordings.

## Retrieve earlier interactions

Replace the example token with one printed by processing:

```powershell
python -m robopii context PERSON_REPLACE_WITH_REAL_TOKEN
python -m robopii recall --type PERSON --value "Jane Smith"
python -m robopii access-log
```

Face tokens and name tokens are independent. Name recall searches detected
names; it does not establish which visible person spoke. Repeated face matching
uses saved encrypted face templates and can reuse a face token across videos.

## Other inputs and configuration

```powershell
python -m robopii process "C:\path\to\audio.wav"
python -m robopii process --text "Email jane@example.com"
python -m robopii --config "C:\path\to\settings.yaml" process "C:\path\to\video.mp4"
```

Audio inputs produce a scrubbed transcript, not a rewritten audio file.
The repository's `config/settings.yaml` is a configuration example. Without
a config file, the wheel uses built-in defaults. `ROBOPII_DATA_DIR` overrides
the database directory. Keep the same data directory and vault secret when
checking returning-face recognition.

See [handover.md](handover.md) for acceptance checks and known limitations.

## Video-only use (without transcription)

```powershell
python -m robopii.video_pipeline "C:\path\to\video.mp4"
```

This command blurs video and stores face tokens without running Whisper.
Use the main `process` command for separate audio inputs.

## Python API

```python
from robopii.pipeline import process_file

result = process_file("recording.wav")  # Or a video path for the combined route
print(result.record_id, result.scrubbed_transcript)
```

For video without transcription:

```python
from robopii.video_pipeline import process_video_only
result = process_video_only("clip.mp4")
print(result.scrubbed_media_path, result.tokens)
```

## Common setup problems

- If ffmpeg is not found, install it and reopen PowerShell so its PATH refreshes.
- If `en_core_web_md` is missing, run `python -m spacy download en_core_web_md`
  using the same Python environment as the application.
- If imports fail, reinstall the wheel with `[all]` in the active environment.
- Whisper's FP16-on-CPU warning means FP32 is used; it is not a failed run.
- First-run Whisper model downloads need an internet connection. Allow them
  to finish before measuring steady-state performance.

Expected video-only output includes a record ID, an MP4 path and `Person tokens`.
Token values and record IDs depend on the local vault and must not be copied
from documentation as expected constants.
