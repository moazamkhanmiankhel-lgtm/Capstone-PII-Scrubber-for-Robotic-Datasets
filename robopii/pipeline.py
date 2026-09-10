"""Main orchestration pipeline for RoboPII."""

from pathlib import Path

from robopii.audio_processor import transcribe_audio
from robopii.models import ProcessingResult
from robopii.storage import (
    initialise_databases,
    save_primary_record,
    save_protected_mapping,
)
from robopii.text_scrubber import scrub_text
from robopii.token_manager import resolve_or_create_token
from robopii.video_scrubber import scrub_video


def process_video(input_path: str) -> ProcessingResult:
    """Process video, audio, text, tokens and storage."""

    initialise_databases()

    input_file = Path(input_path)
    output_path = Path("output") / f"scrubbed_{input_file.name}"

    visual_result = scrub_video(
        input_path=input_path,
        output_path=str(output_path),
    )

    transcript_result = transcribe_audio(input_path)
    text_result = scrub_text(transcript_result.transcript)

    tokens = []

    for detected_entity in text_result.detected_pii:
        mapping = resolve_or_create_token(detected_entity)
        tokens.append(mapping.token)

        save_protected_mapping(
            {
                "token": mapping.token,
                "pii_type": mapping.pii_type,
                "original_value": mapping.original_value,
            }
        )

    primary_record = {
        "scrubbed_media_path": visual_result.output_path,
        "scrubbed_transcript": text_result.scrubbed_text,
        "tokens": tokens,
    }

    record_id = save_primary_record(primary_record)

    return ProcessingResult(
        record_id=record_id,
        scrubbed_media_path=visual_result.output_path,
        scrubbed_transcript=text_result.scrubbed_text,
        tokens=tokens,
    )