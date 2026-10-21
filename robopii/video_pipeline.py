"""Run video scrubbing and persist its face tokens without audio processing."""

import argparse

from robopii.models import ProcessingResult
from robopii.video_scrubber import scrub_video


def process_video_only(input_path: str) -> ProcessingResult:
    """Save a scrubbed MP4 and link it to person tokens in the databases."""
    # Use the same storage, naming and raw-frame cleanup as the full pipeline.
    from pathlib import Path
    from dataclasses import replace
    from robopii.config import get_config
    from robopii.pipeline import Pipeline, PipelineComponents

    settings = get_config()
    settings = replace(settings, pipeline=replace(
        settings.pipeline,
        output_dir=Path.cwd() / "output" / "scrubbed",
        transcribe_video_audio=False,
        tokenize_faces=True,
        write_scrubbed_mp4=True,
    ))
    return Pipeline(settings, PipelineComponents(scrub_video=scrub_video)).process_video(input_path)



def main() -> None:
    parser = argparse.ArgumentParser(description="Scrub video and store face tokens")
    parser.add_argument("video", help="Path to an input video")
    args = parser.parse_args()
    result = process_video_only(args.video)
    print(f"Record ID: {result.record_id}")
    print(f"Scrubbed video: {result.scrubbed_media_path}")
    print(f"Person tokens: {', '.join(result.tokens) or '(none detected)'}")


if __name__ == "__main__":
    main()
