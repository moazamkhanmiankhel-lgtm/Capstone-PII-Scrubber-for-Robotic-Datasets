# Evaluation results and report summary

## Scope and evidence

Results were supplied by the student developer on 23 October 2026 from the
Windows source checkout. Source reviewed: `ee36a64`; the evaluation script was
added locally by the handover patch. Video and audio were tested separately.
No speaking-video acceptance is required. No recordings, original-face frames,
vault files or workstation paths are published with this results summary.
Raw labels/metric JSON remain in the developer's ignored `output/evaluation/`
folders. Preserve those locally as supporting evidence; this document is a
transcription of reported results, not an independently reproduced benchmark.

## Automated functional tests

| Result | Value |
| --- | --- |
| Passed | 180 |
| Skipped | 2 |
| Warnings | 1 |
| Elapsed time | 31.12 s |

The speech fixture requires the macOS `say` command; the file-permission test
uses POSIX permission bits unavailable on Windows. The warning reports Whisper
using FP32 instead of FP16 on CPU. Neither skip is counted as a pass. Separate
synthetic speech and direct audio processing were subsequently exercised below.

## Face detection

One frame was sampled approximately every second. Manual tight face boxes were
matched one-to-one to detector boxes at intersection-over-union >= 0.50.

| Clip | Sampled frames | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| vid1 | 9 | 9 | 0 | 0 | 100% | 100% | 1.00 |
| vid2 | 5 | 5 | 0 | 0 | 100% | 100% | 1.00 |
| Combined | 14 | 14 | 0 | 0 | 100% | 100% | 1.00 |

Initial annotations contained duplicated and overly broad boxes; they were
replaced with tight face annotations and rescored at the unchanged 0.50 IoU
threshold. The corrected labels yielded the table above. Annotation was not
independently reviewed, and correction occurred after initial results were seen.
This is an exploratory student evaluation, not a blinded benchmark.

The recordings were supplied by students within the project group. Both clips
show the same participant, limiting subject diversity and conditions. Frame
samples are correlated; 14 face instances are not 14 independent participants.
The results apply to these samples and should not be generalised to wider
robotic datasets. Larger and more varied datasets are required.

The developer separately inspected vid1's exported MP4 and reported all visible
faces blurred. This is qualitative masking evidence; face-box detection F1
does not measure blur strength or prove that identities cannot be recovered.

## Face recognition and storage

The developer confirmed vid1 and vid2 show the same person. Both runs reused
`PERSON_B7D350E8904A`; context retrieval returned both records:

- vid1: `f461e273-ff0f-43a9-8fd5-36a5d95c84d7`.
- vid2: `059b62eb-ede3-46bf-97c2-bffee856734c`.

This is one successful same-person cross-clip recognition case. Historical
reprocessing records are not additional independent trials. Different-person
recognition was not tested; no false-match rate or general recognition accuracy
is claimed. Face and spoken-name tokens are separate; record co-occurrence
does not verify speaker identity.

## Synthetic audio transcription

One 11.18-second, 16 kHz mono WAV was generated using FFmpeg/libflite's `slt`
voice. Its script includes a fictitious person, location, spoken email and phone.
The reference is the synthesis script; independent listening validation was not
reported. This sample does not represent natural accents, noise or speakers.

| Measurement | Value |
| --- | ---: |
| Reference words | 38 |
| Word edit distance | 16 |
| Raw word error rate | 42.1% |
| Standalone transcription time | 5.3555 s |

WER lowercases word tokens and ignores punctuation, but does not expand digits.
Thus word-number versus digit formatting contributes to edit distance. Retain
this raw score; do not translate it into semantic accuracy. Real errors include
loss of the spoken email's `at` relationship and corruption of the final sentence.

Script:

> My name is Jane Smith. I live in Sydney. My email is jane dot smith at example dot com. My phone number is zero four one two three four five six seven eight. The robot delivered the parcel.

Actual hypothesis:

> My name is Jane Smith. I live in Sydney. My email is Jane.Smith.Example.com. My phone number is 0-412-345678.TheRogue.Deliver to the parcel.

## Text PII detection on the reference

Exact entity type/value matching ignores case and trims outer whitespace.
There is one labelled entity of each type; results are not stable estimates of
per-type performance. This reference-text evaluation isolates detection from
transcription errors.

| Type | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Person | 1 | 0 | 0 | 100% | 100% | 1.00 |
| Location | 1 | 0 | 0 | 100% | 100% | 1.00 |
| Email | 1 | 0 | 0 | 100% | 100% | 1.00 |
| Phone | 0 | 0 | 1 | Undefined | 0% | 0.00 |
| Micro total | 3 | 0 | 1 | 100% | 75% | 0.857 |

The phone expressed as words was missed. Whisper's hypothesis instead used
numeric digits, which were detected and tokenised. Four sensitive spans were
masked in that hypothesis, but no separate automated end-to-end spoken-PII
precision/recall calculation was performed. The lost email meaning is not
repaired by replacing the malformed email with a token.

## Direct audio pipeline and latency

Processing the WAV through `python -m robopii process` created an `audio` record
`c4acaa2b-9092-4d28-80d1-0aa8ad5b1bae`. It stored the scrubbed transcript with
person, location, email and phone tokens. Audio inputs do not export a censored
WAV; the deliverable is the tokenised transcript.

| Run | Stage | Time |
| --- | --- | ---: |
| vid1, main process route | Visual | 155.6545 s |
| vid1, main process route | Transcription | 38.1234 s |
| vid1, main process route | Total before storage | 207.0885 s |
| vid2, video-only route | Visual | 93.7372 s |
| vid2, video-only route | Total before storage | 93.7601 s |
| Direct audio route | Transcription | 4.7844 s |
| Direct audio route | Text scrub | 6.6617 s |
| Direct audio route | Tokenisation | 0.4668 s |
| Direct audio route | Total before storage | 19.8575 s |

These are single runs, not averaged benchmarks. Totals exclude final storage
and stage sums need not equal totals because of other setup/processing costs.
Video-only use avoids transcription for silent video. Different videos and
routes do not establish a speedup. Device specifications/model versions were
not captured in the supplied outputs. No real-time video performance is claimed.

The final primary-store audit passed across 9 records and checked 9 identities.
It checks supported patterns and known vault values; undetected PII can still
escape it. An audit pass does not prove complete anonymisation.

## Deferred checks and conclusion

The team deferred different-person false-match testing and a fresh Windows
installation/end-to-end run of the exact published wheel. An earlier separately
built wheel passed installation, external-directory CLI and model-asset checks.

The evidence supports a functioning research prototype within the evaluated
scope. Broader accuracy, real-world recognition reliability, robust speech
transcription and production security are not established. Future priorities
are spoken-number handling, more diverse labelled data, negative identity
trials and validation of the exact distributed artifact.
