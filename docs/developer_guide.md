# RoboPII Developer Guide

This guide defines the shared functions and variables used by the RoboPII
components. Each member can choose how to implement their component internally,
but they should follow the inputs and outputs in this document.

If a shared function or variable needs to change, discuss it with the team
before changing it.


## Shared Data Models

All components should import shared data models from:

```python
from robopii.models import (
    DetectedPII,
    ProcessingResult,
    TextScrubResult,
    TokenMapping,
    TranscriptResult,
    VisualScrubResult,
)