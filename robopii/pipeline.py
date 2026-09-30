"""Main orchestration pipeline for RoboPII.

The pipeline connects the components in the order agreed in
``docs/architecture.md``:

    input -> visual scrubber -> audio transcription -> text scrubber
          -> token manager -> primary store + protected vault

Raw PII only ever exists in memory while a record is being processed:

* the unscrubbed transcript is never written anywhere;
* the video scrubber's unblurred frames go to a temporary directory that is
  deleted afterwards (unless ``pipeline.keep_original_frames`` is on);
* detected values go to the vault through the token manager, encrypted;
* before anything is written, the scrubbed transcript is checked again for
  every value that was detected, and the primary store runs its own
  pattern check, so a scrubbing mistake stops the record instead of
  storing it.

Components are loaded lazily, so processing a transcript does not need
MediaPipe or Whisper installed, and tests can swap any component for a fake
through ``PipelineComponents``.
"""

import importlib
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from robopii.config import PROJECT_ROOT, Settings, get_config
from robopii.models import (
    DetectedPII,
    ProcessingResult,
    TextScrubResult,
    TokenMapping,
    TranscriptResult,
    VisualScrubResult,
)
from robopii.storage import (
    RawPIIError,
    get_primary_store,
    get_protected_mapping,
    get_protected_vault,
    initialise_databases,
    resolve_primary_db_path,
    save_primary_record,
    validate_primary_record,
)


IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg"})
TEXT_EXTENSIONS = frozenset({".txt"})


class UnsupportedInputError(ValueError):
    """Raised when the pipeline does not know how to process a file."""


def _lazy(module_name: str, function_name: str) -> Callable[..., Any]:
    """Return a function that imports its component on first call."""

    def call(*args, **kwargs):
        module = importlib.import_module(module_name)
        return getattr(module, function_name)(*args, **kwargs)

    call.__name__ = function_name
    call.__qualname__ = f"{module_name}.{function_name}"

    return call


@dataclass
class PipelineComponents:
    """The component functions the pipeline calls.

    Every field defaults to the real implementation agreed in
    ``docs/developer_guide.md``. Tests replace individual fields with fakes.
    """

    scrub_image: Callable[..., VisualScrubResult] = field(
        default_factory=lambda: _lazy("robopii.video_scrubber", "scrub_image")
    )
    scrub_video: Callable[..., VisualScrubResult] = field(
        default_factory=lambda: _lazy("robopii.video_scrubber", "scrub_video")
    )
    transcribe_audio: Callable[[str], TranscriptResult] = field(
        default_factory=lambda: _lazy(
            "robopii.audio_processor", "transcribe_audio"
        )
    )
    scrub_text: Callable[[str], TextScrubResult] = field(
        default_factory=lambda: _lazy("robopii.text_scrubber", "scrub_text")
    )
    resolve_or_create_token: Callable[[DetectedPII], TokenMapping] = field(
        default_factory=lambda: _lazy(
            "robopii.token_manager", "resolve_or_create_token"
        )
    )


@dataclass
class _TextOutcome:
    """Scrubbed, tokenised transcript ready for storage."""

    scrubbed_text: str
    tokens: list[str]
    pii_counts: dict[str, int]
    scrub_seconds: float
    token_seconds: float


def detect_input_type(input_path: str | Path) -> str:
    """Return ``image``, ``video``, ``audio`` or ``transcript``."""
    suffix = Path(input_path).suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        return "image"

    if suffix in VIDEO_EXTENSIONS:
        return "video"

    if suffix in AUDIO_EXTENSIONS:
        return "audio"

    if suffix in TEXT_EXTENSIONS:
        return "transcript"

    supported = sorted(
        IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | TEXT_EXTENSIONS
    )

    raise UnsupportedInputError(
        f"Unsupported file type {suffix or '(none)'!r}. "
        f"Supported: {', '.join(supported)}"
    )


# Values shorter than this are matched case-sensitively, so a detected
# location such as "US" is not reported as leaked by the word "us".
CASE_INSENSITIVE_MIN_LENGTH = 4


def _value_pattern(value: str) -> re.Pattern[str]:
    """Match a raw value as a whole word.

    Case is ignored for values of four or more characters, which catches
    "jane" left behind after "Jane" was scrubbed.
    """
    value = value.strip()
    flags = re.IGNORECASE if len(value) >= CASE_INSENSITIVE_MIN_LENGTH else 0

    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", flags)


def find_leaked_values(
    text: str | None,
    detected: list[DetectedPII],
) -> list[DetectedPII]:
    """Return the detected entities whose original value is still in text."""
    if not text:
        return []

    return [
        entity
        for entity in detected
        if entity.original_value
        and entity.original_value.strip()
        and _value_pattern(entity.original_value).search(text)
    ]


def storable_media_path(path: str | Path) -> str:
    """Return the media path in the form stored in the primary store.

    Paths inside the project are stored relative to the project root, for
    example ``output/scrubbed/<record_id>``. An absolute path would carry
    the home folder, and on most machines the home folder is the user's
    name (``C:\\Users\\Andre\\...``), which is PII.

    Paths outside the project are kept as given; ``audit_primary_store``
    will report them if they contain a known identity.
    """
    resolved = Path(path).resolve()

    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def link_placeholders_to_tokens(
    scrubbed_text: str,
    placeholder_tokens: dict[str, str],
) -> str:
    """Replace ``[PERSON_1]`` style placeholders with ``[<token>]``.

    Done in a single pass so ``[PERSON_1]`` never touches ``[PERSON_10]``.
    """
    if not placeholder_tokens:
        return scrubbed_text

    pattern = re.compile(
        "|".join(
            re.escape(placeholder)
            for placeholder in sorted(placeholder_tokens, key=len, reverse=True)
        )
    )

    return pattern.sub(
        lambda match: f"[{placeholder_tokens[match.group(0)]}]",
        scrubbed_text,
    )


class Pipeline:
    """Process one input into a stored, de-identified record."""

    def __init__(
        self,
        settings: Settings | None = None,
        components: PipelineComponents | None = None,
    ):
        self.settings = settings or get_config()
        self.components = components or PipelineComponents()

    # Public entry points -------------------------------------------------

    def process_file(self, input_path: str | Path) -> ProcessingResult:
        """Process any supported file, choosing the route by extension."""
        input_type = detect_input_type(input_path)

        if input_type == "image":
            return self.process_image(input_path)

        if input_type == "video":
            return self.process_video(input_path)

        if input_type == "audio":
            return self.process_audio(input_path)

        return self.process_transcript_file(input_path)

    def process_image(self, input_path: str | Path) -> ProcessingResult:
        """Blur faces in an image and store the record."""
        input_file = self._require_file(input_path)
        record_id = str(uuid4())
        output_dir = self._record_output_dir(record_id)
        output_file = output_dir / f"image{input_file.suffix.lower()}"

        started_at = perf_counter()

        try:
            visual_result = self.components.scrub_image(
                input_path=str(input_file),
                output_path=str(output_file),
            )

            return self._store(
                record_id=record_id,
                input_type="image",
                started_at=started_at,
                visual_result=visual_result,
                text_outcome=None,
                audio_status="not_applicable",
            )
        except BaseException:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise

    def process_video(self, input_path: str | Path) -> ProcessingResult:
        """Blur faces in every frame, transcribe and scrub the audio."""
        input_file = self._require_file(input_path)
        record_id = str(uuid4())
        output_dir = self._record_output_dir(record_id)
        original_dir = self._original_frames_dir(record_id)

        started_at = perf_counter()

        try:
            # Face tokens are looked up in, and saved to, the protected
            # vault by the video scrubber, so the databases must exist.
            if self.settings.pipeline.tokenize_faces:
                initialise_databases()

            visual_result = self.components.scrub_video(
                input_path=str(input_file),
                output_path=str(output_dir),
                original_output_dir=str(original_dir),
                tokenize_faces=self.settings.pipeline.tokenize_faces,
                output_video_path=(
                    str(output_dir / "scrubbed.mp4")
                    if self.settings.pipeline.write_scrubbed_mp4
                    else None
                ),
            )

            text_outcome = None
            transcript_seconds = None
            audio_status = "skipped"

            if self.settings.pipeline.transcribe_video_audio:
                try:
                    transcript_result = self.components.transcribe_audio(
                        str(input_file)
                    )
                except ValueError:
                    # The audio processor raises ValueError when the video
                    # has no usable audio track. The frames are still worth
                    # keeping, so the record is stored without a transcript.
                    audio_status = "no_audio"
                except (NotImplementedError, ImportError):
                    # Transcription is not available on this machine (the
                    # component is a stub, or Whisper is not installed).
                    # Store the blurred frames and say so in the metadata.
                    audio_status = "transcriber_unavailable"
                else:
                    audio_status = "transcribed"
                    transcript_seconds = (
                        transcript_result.processing_time_seconds
                    )
                    text_outcome = self._scrub_and_tokenise(
                        transcript_result.transcript
                    )

            return self._store(
                record_id=record_id,
                input_type="video",
                started_at=started_at,
                visual_result=visual_result,
                text_outcome=text_outcome,
                audio_status=audio_status,
                transcript_seconds=transcript_seconds,
            )
        except BaseException:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise
        finally:
            if not self.settings.pipeline.keep_original_frames:
                shutil.rmtree(original_dir, ignore_errors=True)

    def process_audio(self, input_path: str | Path) -> ProcessingResult:
        """Transcribe an audio file and store the scrubbed transcript."""
        input_file = self._require_file(input_path)
        started_at = perf_counter()

        transcript_result = self.components.transcribe_audio(str(input_file))
        text_outcome = self._scrub_and_tokenise(transcript_result.transcript)

        return self._store(
            record_id=str(uuid4()),
            input_type="audio",
            started_at=started_at,
            visual_result=None,
            text_outcome=text_outcome,
            audio_status="transcribed",
            transcript_seconds=transcript_result.processing_time_seconds,
        )

    def process_transcript(self, text: str) -> ProcessingResult:
        """Scrub and store a transcript that is already text."""
        if not isinstance(text, str):
            raise TypeError("Transcript must be a string.")

        started_at = perf_counter()
        text_outcome = self._scrub_and_tokenise(text)

        return self._store(
            record_id=str(uuid4()),
            input_type="transcript",
            started_at=started_at,
            visual_result=None,
            text_outcome=text_outcome,
            audio_status="not_applicable",
        )

    def process_transcript_file(
        self,
        input_path: str | Path,
    ) -> ProcessingResult:
        """Scrub and store a UTF-8 text transcript file."""
        input_file = self._require_file(input_path)

        return self.process_transcript(
            input_file.read_text(encoding="utf-8")
        )

    # Internal steps ------------------------------------------------------

    @staticmethod
    def _require_file(input_path: str | Path) -> Path:
        input_file = Path(input_path)

        if not input_file.is_file():
            raise FileNotFoundError(f"Input file does not exist: {input_file}")

        return input_file

    def _record_output_dir(self, record_id: str) -> Path:
        """Folder for this record's scrubbed media.

        Named after the record ID rather than the input file, because file
        names can contain PII and this path is stored in the primary store.
        """
        return self.settings.pipeline.output_dir / record_id

    def _original_frames_dir(self, record_id: str) -> Path:
        """Folder the video scrubber writes unblurred frames to."""
        if self.settings.pipeline.keep_original_frames:
            from robopii.storage import resolve_data_dir

            return resolve_data_dir() / "original_frames" / record_id

        return Path(tempfile.mkdtemp(prefix="robopii_originals_"))

    def _scrub_and_tokenise(self, text: str) -> _TextOutcome:
        """Scrub a transcript and swap detected values for tokens."""
        scrub_started = perf_counter()
        text_result = self.components.scrub_text(text)
        scrub_seconds = perf_counter() - scrub_started

        token_started = perf_counter()
        tokens: list[str] = []
        placeholder_tokens: dict[str, str] = {}
        pii_counts: dict[str, int] = {}

        for entity in text_result.detected_pii:
            # resolve_or_create_token saves the encrypted mapping in the
            # vault itself, so no separate save_protected_mapping is needed.
            mapping = self.components.resolve_or_create_token(entity)

            if mapping.token not in tokens:
                tokens.append(mapping.token)

            placeholder_tokens[entity.placeholder] = mapping.token
            pii_counts[entity.pii_type] = pii_counts.get(entity.pii_type, 0) + 1

        scrubbed_text = text_result.scrubbed_text

        if self.settings.pipeline.link_tokens_in_transcript:
            scrubbed_text = link_placeholders_to_tokens(
                scrubbed_text,
                placeholder_tokens,
            )

        token_seconds = perf_counter() - token_started

        if self.settings.pipeline.verify_no_raw_pii:
            leaked = find_leaked_values(scrubbed_text, text_result.detected_pii)

            if leaked:
                leaked_types = sorted({entity.pii_type for entity in leaked})
                # The values themselves are not put in the message, since
                # error messages end up in logs and terminals.
                raise RawPIIError(
                    "Refusing to store the transcript: detected "
                    f"{', '.join(leaked_types)} value(s) are still present "
                    "after scrubbing."
                )

        return _TextOutcome(
            scrubbed_text=scrubbed_text,
            tokens=tokens,
            pii_counts=pii_counts,
            scrub_seconds=scrub_seconds,
            token_seconds=token_seconds,
        )

    def _store(
        self,
        record_id: str,
        input_type: str,
        started_at: float,
        visual_result: VisualScrubResult | None,
        text_outcome: _TextOutcome | None,
        audio_status: str,
        transcript_seconds: float | None = None,
    ) -> ProcessingResult:
        """Write the scrubbed record to the primary store."""
        initialise_databases()

        timings: dict[str, float] = {}

        if visual_result is not None:
            timings["visual"] = round(visual_result.processing_time_seconds, 4)

        if transcript_seconds is not None:
            timings["transcription"] = round(transcript_seconds, 4)

        if text_outcome is not None:
            timings["text_scrub"] = round(text_outcome.scrub_seconds, 4)
            timings["tokenisation"] = round(text_outcome.token_seconds, 4)

        timings["total_before_storage"] = round(perf_counter() - started_at, 4)

        # Face tokens come from the video scrubber (one per distinct face),
        # text tokens from the transcript. A person seen and heard has one
        # of each; they are linked only by appearing in the same records.
        face_tokens = list(
            getattr(visual_result, "tokens", None) or []
        )
        text_tokens = [] if text_outcome is None else text_outcome.tokens
        tokens = list(dict.fromkeys(face_tokens + text_tokens))

        metadata: dict[str, Any] = {
            "input_type": input_type,
            "audio_status": audio_status,
            "faces_detected": (
                None if visual_result is None else visual_result.faces_detected
            ),
            "face_tokens": len(face_tokens),
            "pii_counts": {} if text_outcome is None else text_outcome.pii_counts,
            "timings_seconds": timings,
        }

        scrubbed_media_path = (
            None
            if visual_result is None
            else storable_media_path(visual_result.output_path)
        )
        scrubbed_transcript = (
            None if text_outcome is None else text_outcome.scrubbed_text
        )

        stored_id = save_primary_record(
            {
                "record_id": record_id,
                "scrubbed_media_path": scrubbed_media_path,
                "scrubbed_transcript": scrubbed_transcript,
                "source_label": input_type,
                "metadata": metadata,
                "tokens": tokens,
            }
        )

        return ProcessingResult(
            record_id=stored_id,
            scrubbed_media_path=scrubbed_media_path,
            scrubbed_transcript=scrubbed_transcript,
            tokens=list(tokens),
        )


# Module level helpers -----------------------------------------------------
#
# These keep the function-style interface the rest of the team already uses
# (``process_video(input_path)``) while delegating to ``Pipeline``.


def process_file(input_path: str) -> ProcessingResult:
    """Process any supported input file."""
    return Pipeline().process_file(input_path)


def process_video(input_path: str) -> ProcessingResult:
    """Process video, audio, text, tokens and storage."""
    return Pipeline().process_video(input_path)


def process_image(input_path: str) -> ProcessingResult:
    """Process a single image."""
    return Pipeline().process_image(input_path)


def process_audio(input_path: str) -> ProcessingResult:
    """Process an audio recording."""
    return Pipeline().process_audio(input_path)


def process_transcript(text: str) -> ProcessingResult:
    """Process a transcript that is already text."""
    return Pipeline().process_transcript(text)


# Raw PII audit ------------------------------------------------------------


@dataclass
class AuditReport:
    """Result of checking the primary store for raw PII.

    Problems name the record or token involved, never the raw value.
    """

    records_checked: int = 0
    identities_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def audit_primary_store(
    actor: str = "audit",
    reason: str = "primary store raw PII audit",
) -> AuditReport:
    """Check that the primary store holds no raw PII.

    Three checks:

    1. Every stored record passes the primary store's own validation
       (field whitelist, token shape, pattern-based PII guard).
    2. No identity value held in the vault appears in any stored record.
       This is the check that catches plain names, which the pattern guard
       cannot see.
    3. No identity value appears anywhere in the raw bytes of
       ``primary.db``, including free pages left behind by SQLite.

    Check 2 and 3 decrypt every vault value, so each lookup is written to
    the vault access log under ``actor`` and ``reason``.
    """
    initialise_databases()

    report = AuditReport()
    records = get_primary_store().list_records()
    report.records_checked = len(records)

    for record in records:
        try:
            validate_primary_record(record)
        except ValueError as error:
            report.problems.append(
                f"Record {record['record_id']} failed validation: "
                f"{type(error).__name__}"
            )

    primary_path = resolve_primary_db_path()
    raw_bytes = (
        primary_path.read_bytes().decode("utf-8", errors="ignore")
        if primary_path.is_file()
        else ""
    )

    for token in get_protected_vault().list_tokens():
        mapping = get_protected_mapping(token, actor=actor, reason=reason)

        if mapping is None or not str(mapping.original_value).strip():
            continue

        report.identities_checked += 1
        pattern = _value_pattern(str(mapping.original_value))

        for record in records:
            searchable = " ".join(
                str(value)
                for value in (
                    record.get("scrubbed_transcript"),
                    record.get("scrubbed_media_path"),
                    record.get("source_label"),
                    record.get("metadata"),
                )
                if value
            )

            if pattern.search(searchable):
                report.problems.append(
                    f"Record {record['record_id']} contains the raw value "
                    f"behind {token}."
                )

        if pattern.search(raw_bytes):
            report.problems.append(
                f"primary.db file contains the raw value behind {token}."
            )

    return report
