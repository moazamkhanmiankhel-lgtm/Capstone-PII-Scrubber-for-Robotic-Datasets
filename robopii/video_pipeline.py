"""Run video scrubbing and persist its face tokens without audio processing."""

import argparse
from pathlib import Path

from robopii.models import ProcessingResult
from robopii.storage import initialise_databases, save_primary_record
from robopii.video_scrubber import scrub_video


def process_video_only(input_path: str) -> ProcessingResult:
    """Save scrubbed video frames and their person tokens in the databases."""
    input_file = Path(input_path)
    if not input_file.is_file():
        raise FileNotFoundError(f"Input video does not exist: {input_file}")

    initialise_databases()
    output_path = Path("output") / "scrubbed_frames" / input_file.stem
    visual = scrub_video(
        str(input_file), str(output_path), tokenize_faces=True,
    )
    record_id = save_primary_record({
        "scrubbed_media_path": visual.output_path,
        "tokens": visual.tokens,
        "source_label": input_file.name,
    })
    return ProcessingResult(
        record_id=record_id,
        scrubbed_media_path=visual.output_path,
        scrubbed_transcript=None,
        tokens=visual.tokens,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrub video and store face tokens")
    parser.add_argument("video", help="Path to an input video")
    args = parser.parse_args()
    result = process_video_only(args.video)
    print(f"Record ID: {result.record_id}")
    print(f"Scrubbed frames: {result.scrubbed_media_path}")
    print(f"Person tokens: {', '.join(result.tokens) or '(none detected)'}")


if __name__ == "__main__":
    main()
