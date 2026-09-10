"""Visual PII detection and obfuscation."""

from robopii.models import VisualScrubResult


def scrub_image(
    input_path: str,
    output_path: str
) -> VisualScrubResult:
    """Detect and obscure faces in an image."""
    raise NotImplementedError


def scrub_video(
    input_path: str,
    output_path: str
) -> VisualScrubResult:
    """Detect and obscure faces in each video frame."""
    raise NotImplementedError