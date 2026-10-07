"""Run video scrubbing and persist its face tokens without audio processing.

This is the video-only entry point. It goes through the main
``robopii.pipeline.Pipeline`` with transcription switched off, so a
video-only record gets the same protections as any other record:

* unblurred frames are written to a temporary folder and deleted;
* output folders are named after the record ID, not the input file, and the
  input file name is never stored (file names can contain names);
* the stored media path is relative to the project root;
* settings come from ``config/settings.yaml``, and per-stage timings are
  stored with the record.

Equivalent command: ``python main.py process <video> --no-audio``.
"""

import argparse
from dataclasses import replace

from robopii.config import Settings, get_config
from robopii.models import ProcessingResult
from robopii.pipeline import Pipeline, PipelineComponents


def video_only_settings(settings: Settings | None = None) -> Settings:
    """Return settings for a video-only run: face tokens on, no audio."""
    settings = settings or get_config()

    return replace(
        settings,
        pipeline=replace(
            settings.pipeline,
            transcribe_video_audio=False,
            tokenize_faces=True,
        ),
    )


def process_video_only(
    input_path: str,
    settings: Settings | None = None,
    components: PipelineComponents | None = None,
) -> ProcessingResult:
    """Save a scrubbed video and link it to person tokens in the databases."""
    return Pipeline(
        video_only_settings(settings),
        components,
    ).process_video(input_path)


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
