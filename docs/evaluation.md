# Labelled evaluation for the report

Video and audio are evaluated separately. Speaking video is not an acceptance
requirement. Run the commands below from the repository root with the full
project dependencies installed. This tooling is source-only, outside the wheel.
Evaluation outputs may contain original faces and PII; keep them in ignored
`output/` and use consenting participants and synthetic contact details.

## 1. Silent video: annotate independently, then score

```powershell
py tools/evaluate.py video "sample_data\videos\vid1.mp4" --interval 1 --out output/evaluation/vid1
py tools/evaluate.py label --out output/evaluation/vid1
py tools/evaluate.py score --out output/evaluation/vid1
```

Preparation runs the same detector configuration in VIDEO mode on every frame,
then samples one frame per second for scoring. It does not run face recognition
or change the vault. A pre-existing labels file is preserved; select a fresh
output directory to prepare a new evaluation. Labelling resumes saved progress.

In each original frame, draw a tight rectangle around every visible face.
SPACE/ENTER accepts each rectangle; ESC finishes the frame. Press Y at the
confirmation window to save, R to redraw or Q to stop. Saving zero boxes means
that you inspected the frame and found no faces. Do not label only the faces
the system found. Pick and document a consistent rule for partial/tiny faces.
Keep display scaling consistent and ensure the whole frame is visible.

`metrics.json` reports raw TP, FP, FN and precision, recall and F1. Matching is
one-to-one at box intersection-over-union >= 0.5. Null means undefined, not
zero. A face instance in each frame is a separate observation; correlated
samples are not independent people. Report video count, labelled frame count,
face-instance count and sample interval with results. The detector score does
not establish blur adequacy: manually inspect masked output separately and
report that as a qualitative check, unless you label blur adequacy per face.
Repeat on vid2 with a different output directory. Include no-face clips to
check false detections. Do not tune thresholds on the evaluation clips and
then describe their results as independent validation.

## 2. Recognition: independent same/different-person trials

Current evidence: vid1 and vid2 show the same person and reused
`PERSON_B7D350E8904A`. This is one successful same-person trial. Repeated
processing and old database interaction counts are not additional trials.

Use a single clearly visible participant per clip. Start an isolated evaluation
vault (choose a new, unused directory), enroll person A, then process another
clip of A and a clip of person B:

```powershell
$env:ROBOPII_DATA_DIR = "$PWD\output\evaluation\recognition-data"
py -m robopii.video_pipeline "sample_data\videos\vid1.mp4"
py -m robopii.video_pipeline "sample_data\videos\vid2.mp4"
# Replace this with a real clip of a DIFFERENT consenting participant:
py -m robopii.video_pipeline "C:\path\to\different_person.mp4"
Remove-Item Env:ROBOPII_DATA_DIR
```

Record the actual tokens per clip and known participant IDs. A matching token
for A's second clip is a correct return; B sharing A's token is a false match.
Report correct returns / known same-person trials and false matches / known
different-person trials, with counts. Multiple tokens for one person are an
identity split and must be recorded. More people/conditions are needed for a
credible aggregate estimate. Remove the environment override even if a command
fails. Existing output video files are not deleted by switching databases.

## 3. Audio transcription, separately

Manually transcribe the recording before viewing Whisper output. Save the
reference as UTF-8 under `output/evaluation/audio/reference.txt`.

```powershell
py tools/evaluate.py audio "C:\path\to\audio.wav" --reference output/evaluation/audio/reference.txt --out output/evaluation/audio
```

`audio_metrics.json` records word edit distance, reference word count, WER and
transcription time. `hypothesis.txt` holds the actual transcript. WER is edits /
reference words; it can exceed 1. Punctuation and case are ignored, digits are
not expanded. State how spoken numbers were transcribed. Weight aggregate WER
by total reference words, not by averaging clip percentages.

## 4. Text PII, separately and after transcription

Create a JSON list manually, covering every in-scope entity in each text.
Example schema (an illustration, not a completed evaluation dataset):

```json
[
  {"text": "Email jane@example.com", "entities": [
    {"type": "EMAIL", "value": "jane@example.com"}
  ]},
  {"text": "The robot moved forward", "entities": []}
]
```

Save it as `output/evaluation/text/labels.json`, then run:

```powershell
py tools/evaluate.py text --labels output/evaluation/text/labels.json --out output/evaluation/text
```

Include names, locations, emails, phone numbers and clean negative cases.
`pii_metrics.json` reports per-type and micro precision/recall/F1 with counts.
Scoring uses exact type/value matches after case-folding and trimming; document
this rule. Label the actual Whisper hypothesis separately to evaluate the text
scrubber on transcription output. This is not end-to-end spoken PII recall:
entities omitted by transcription must also count as misses in a separate
comparison against the original recording. Do not claim end-to-end accuracy
from WER and text F1 alone.

## Report evidence

Retain labels, input identifiers, commands, source commit, model versions,
threshold, device, denominators and JSON results with the handover. Include
precision/recall/F1, WER, recognition trial counts, qualitative masking checks
and per-stage latency. Compare processing times only with clip duration and
configuration stated. Existing timings: vid1 visual 155.6545 s (full route),
vid2 visual 93.7372 s (video-only route); differing clips are not a speedup test.

Completed results and limitations are in [evaluation_results.md](evaluation_results.md).
Different-person recognition and fresh published-wheel Windows acceptance were
explicitly deferred for this handover; instructions above remain available for
future evaluation.
