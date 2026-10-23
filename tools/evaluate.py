"""Local labelled evaluation. Run from repository root; results stay in output/."""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2), encoding="utf-8")


def metrics(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return dict(tp=tp, fp=fp, fn=fn, precision=p, recall=r,
                f1=(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None))


def matching(predictions, truth, threshold, overlap):
    # Maximum cardinality one-to-one matching, rather than order-sensitive greedy matching.
    edges = [[j for j, gt in enumerate(truth) if overlap(box, gt) >= threshold]
             for box in predictions]
    assigned = {}
    def visit(i, seen):
        for j in edges[i]:
            if j not in seen:
                seen.add(j)
                if j not in assigned or visit(assigned[j], seen):
                    assigned[j] = i
                    return True
        return False
    tp = sum(visit(i, set()) for i in range(len(predictions)))
    return tp, len(predictions) - tp, len(truth) - tp


def video(args):
    import cv2
    import mediapipe as mp
    from robopii.video_scrubber import _create_face_detector, _detect_faces
    source = Path(args.video).resolve()
    folder = Path(args.out)
    labels_path = folder / "labels.json"
    if labels_path.exists():
        raise ValueError("Output already has labels. Use another --out folder; existing labels are preserved.")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError("Cannot open input video")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        raise ValueError("Video has no valid FPS")
    sample_every = max(1, round(fps * args.interval))
    folder.mkdir(parents=True, exist_ok=True)
    samples = []
    number = 0
    # Run every frame, retaining detector VIDEO state as the video pipeline does.
    try:
        with _create_face_detector(mp.tasks.vision.RunningMode.VIDEO) as detector:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                number += 1
                predicted = _detect_faces(frame, detector, int(number * 1000 / fps))
                if (number - 1) % sample_every:
                    continue
                name = f"frame_{number:06d}.png"
                if not cv2.imwrite(str(folder / name), frame):
                    raise OSError("Could not save sample frame")
                samples.append(dict(frame=number, image=name, boxes=None))
                # Predictions intentionally kept separate from manual labels.
                write(folder / f"prediction_{number:06d}.json", predicted)
    finally:
        capture.release()
    if not samples:
        raise ValueError("No frames decoded")
    write(labels_path, dict(video=str(source), fps=fps, decoded_frames=number,
                            interval_seconds=args.interval, frames=samples))
    print(f"Prepared {len(samples)} frames. Next: python tools/evaluate.py label --out {folder}")


def label(args):
    import cv2
    folder = Path(args.out)
    path = folder / "labels.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    print("Draw ALL face boxes. SPACE/ENTER confirms each box; ESC finishes each frame.")
    print("After drawing: Y saves this frame, R redraws, Q quits without marking it complete.")
    try:
        for item in data["frames"]:
            if item["boxes"] is not None:
                continue
            frame = cv2.imread(str(folder / item["image"]))
            if frame is None:
                raise ValueError("Missing sample frame")
            while True:
                title = f"Frame {item['frame']}: label all faces, ESC when finished"
                boxes = cv2.selectROIs(title, frame, showCrosshair=True, fromCenter=False)
                cv2.destroyAllWindows()
                preview = frame.copy()
                for x, y, w, h in boxes:
                    cv2.rectangle(preview, (int(x), int(y)), (int(x+w), int(y+h)), (0,255,0), 2)
                cv2.imshow("Y save / R redraw / Q quit (Y with no boxes means no faces)", preview)
                key = cv2.waitKey(0) & 0xFF
                cv2.destroyAllWindows()
                if key in (ord('q'), ord('Q')):
                    return
                if key in (ord('y'), ord('Y')):
                    item["boxes"] = [[int(v) for v in b] for b in boxes if b[2] > 0 and b[3] > 0]
                    write(path, data)
                    break
    finally:
        cv2.destroyAllWindows()
    print("Labels complete. Run score.")


def score(args):
    from robopii.video_scrubber import _box_overlap
    folder = Path(args.out)
    data = json.loads((folder / "labels.json").read_text(encoding="utf-8"))
    if any(f["boxes"] is None for f in data["frames"]):
        raise ValueError("Some frames are unlabelled. Finish label first.")
    tp = fp = fn = 0
    for item in data["frames"]:
        pred = json.loads((folder / f"prediction_{item['frame']:06d}.json").read_text())
        a, b, c = matching(pred, item["boxes"], args.iou, _box_overlap)
        tp, fp, fn = tp+a, fp+b, fn+c
    result = dict(metric="sampled face-box detection", video=data["video"],
                  sampled_frames=len(data["frames"]), iou_threshold=args.iou,
                  **metrics(tp, fp, fn),
                  limitation="Sampled frames are correlated. Detection does not measure blur strength or identity recognition.")
    write(folder / "metrics.json", result)
    print(json.dumps(result, indent=2))


def wer(reference, hypothesis):
    # Lowercase words, ignoring punctuation; preserve digits as written.
    words = lambda s: re.findall(r"\w+", s.casefold())
    a, b = words(reference), words(hypothesis)
    if not a:
        raise ValueError("Reference transcript must contain words")
    row = list(range(len(b)+1))
    for i, word in enumerate(a, 1):
        new = [i]
        for j, other in enumerate(b, 1):
            new.append(min(new[-1]+1, row[j]+1, row[j-1]+(word != other)))
        row = new
    return dict(reference_words=len(a), word_edit_distance=row[-1], word_error_rate=row[-1]/len(a))


def audio(args):
    from robopii.audio_processor import transcribe_audio
    reference = Path(args.reference).read_text(encoding="utf-8")
    transcription = transcribe_audio(args.audio)
    result = dict(audio=args.audio, **wer(reference, transcription.transcript),
                  transcription_seconds=transcription.processing_time_seconds,
                  normalization="Case-insensitive word tokens, punctuation ignored; digits not expanded.")
    folder = Path(args.out)
    write(folder / "audio_metrics.json", result)
    folder.joinpath("hypothesis.txt").write_text(transcription.transcript, encoding="utf-8")
    print(json.dumps(result, indent=2))


def text(args):
    from collections import Counter
    from robopii.text_scrubber import scrub_text
    cases = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if not cases:
        raise ValueError("Need at least one labelled case")
    totals = {}
    for case in cases:
        expected = Counter((e["type"].upper(), e["value"].strip().casefold()) for e in case["entities"])
        actual = Counter((e.pii_type.upper(), e.original_value.strip().casefold())
                         for e in scrub_text(case["text"]).detected_pii)
        for kind in {k[0] for k in expected} | {k[0] for k in actual}:
            counts = totals.setdefault(kind, [0, 0, 0])
            truth = Counter({k: v for k, v in expected.items() if k[0] == kind})
            pred = Counter({k: v for k, v in actual.items() if k[0] == kind})
            counts[0] += sum((truth & pred).values())
            counts[1] += sum((pred - truth).values())
            counts[2] += sum((truth - pred).values())
    combined = [sum(x[i] for x in totals.values()) for i in range(3)]
    result = dict(cases=len(cases), matching="Exact entity type and value; whitespace trimmed, case ignored",
                  per_type={k: metrics(*v) for k, v in totals.items()}, micro=metrics(*combined))
    write(Path(args.out) / "pii_metrics.json", result)
    print(json.dumps(result, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("video")
    v.add_argument("video")
    v.add_argument("--interval", type=float, default=1.0)
    v.add_argument("--out", required=True)
    v.set_defaults(run=video)
    for name, function in [("label", label), ("score", score)]:
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        s.set_defaults(run=function)
        if name == "score":
            s.add_argument("--iou", type=float, default=0.5)
    a = sub.add_parser("audio")
    a.add_argument("audio")
    a.add_argument("--reference", required=True)
    a.add_argument("--out", required=True)
    a.set_defaults(run=audio)
    t = sub.add_parser("text")
    t.add_argument("--labels", required=True)
    t.add_argument("--out", required=True)
    t.set_defaults(run=text)
    args = p.parse_args()
    if getattr(args, "interval", 1) <= 0 or not 0 < getattr(args, "iou", 0.5) <= 1:
        p.error("Interval must be positive and IoU must be in (0, 1].")
    try:
        args.run(args)
    except (ValueError, OSError) as error:
        p.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
