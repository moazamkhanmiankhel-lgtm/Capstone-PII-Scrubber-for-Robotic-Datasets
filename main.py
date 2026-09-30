"""RoboPII command-line demonstration.

Examples::

    python main.py process sample_data/videos/clip.mp4
    python main.py process --text "Hi, I'm Jane Smith, call me on 0412 345 678"
    python main.py records
    python main.py context PERSON_A1B2C3D4E5F6
    python main.py recall --type PERSON --value "Jane Smith"
    python main.py reveal PERSON_A1B2C3D4E5F6 --actor operator --reason "returning visitor"
    python main.py forget PERSON_A1B2C3D4E5F6 --actor operator --reason "consent withdrawn"
    python main.py access-log
    python main.py audit

Add ``--config path/to/settings.yaml`` before the command to use a
different settings file.
"""

import argparse
import json
import sys

from robopii.config import (
    ConfigError,
    apply_storage_settings,
    load_config,
    set_config,
)


def _print_result(result) -> None:
    print(f"Record ID:        {result.record_id}")
    print(f"Scrubbed media:   {result.scrubbed_media_path or '-'}")
    print(f"Tokens:           {', '.join(result.tokens) or '-'}")
    print("Scrubbed transcript:")
    print(f"  {result.scrubbed_transcript or '-'}")


def _print_record(record: dict) -> None:
    metadata = record.get("metadata") or {}
    print(f"- {record['record_id']}  ({record['created_at']})")
    print(f"    type:       {metadata.get('input_type', record.get('source_label'))}")
    print(f"    tokens:     {', '.join(record.get('tokens') or []) or '-'}")
    print(f"    media:      {record.get('scrubbed_media_path') or '-'}")
    print(f"    transcript: {record.get('scrubbed_transcript') or '-'}")

    timings = metadata.get("timings_seconds")

    if timings:
        print(f"    timings:    {json.dumps(timings)}")


def command_process(args) -> int:
    from robopii.pipeline import Pipeline

    pipeline = Pipeline()

    if args.text is not None:
        result = pipeline.process_transcript(args.text)
    elif args.path:
        result = pipeline.process_file(args.path)
    else:
        print("Give a file path or --text.", file=sys.stderr)
        return 2

    _print_result(result)

    from robopii.storage import get_primary_record

    audio_status = (get_primary_record(result.record_id) or {}).get(
        "metadata", {}
    ).get("audio_status")

    if audio_status == "no_audio":
        print("Note: no audio track found, so no transcript was stored.")
    elif audio_status == "transcriber_unavailable":
        print(
            "Warning: transcription is not available (audio_processor not "
            "implemented or Whisper not installed). Only frames were stored."
        )

    return 0


def command_records(args) -> int:
    from robopii.storage import get_primary_store, initialise_databases

    initialise_databases()
    records = get_primary_store().list_records(limit=args.limit)

    if not records:
        print("No records stored yet.")
        return 0

    for record in records:
        _print_record(record)

    return 0


def command_context(args) -> int:
    from robopii.retrieval import build_context

    summary = build_context(args.token, limit=args.limit)

    print(f"Token:        {summary.token}")
    print(f"Interactions: {summary.interaction_count}")
    print(f"First seen:   {summary.first_seen or '-'}")
    print(f"Last seen:    {summary.last_seen or '-'}")
    print(f"Seen with:    {', '.join(summary.related_tokens) or '-'}")
    print("Records (newest first):")

    for record in summary.records:
        _print_record(record)

    return 0


def command_recall(args) -> int:
    from robopii.retrieval import recall_entity

    token, records = recall_entity(args.type, args.value, limit=args.limit)

    if token is None:
        print("Not seen before. No context available.")
        return 0

    print(f"Seen before as {token} in {len(records)} record(s):")

    for record in records:
        _print_record(record)

    return 0


def command_reveal(args) -> int:
    from robopii.retrieval import AccessDeniedError, resolve_identity

    try:
        mapping = resolve_identity(args.token, actor=args.actor, reason=args.reason)
    except AccessDeniedError as error:
        print(f"Access denied: {error} (attempt logged)", file=sys.stderr)
        return 3

    if mapping is None:
        print("No identity is held for that token.")
        return 1

    print(f"{mapping.token} -> {mapping.pii_type}: {mapping.original_value}")
    print("This lookup has been written to the vault access log.")
    return 0


def command_forget(args) -> int:
    from robopii.retrieval import AccessDeniedError, forget_identity

    try:
        removed = forget_identity(args.token, actor=args.actor, reason=args.reason)
    except AccessDeniedError as error:
        print(f"Access denied: {error} (attempt logged)", file=sys.stderr)
        return 3

    if removed:
        print(
            f"Identity behind {args.token} deleted. Its records remain but "
            "can no longer be linked to a person."
        )
        return 0

    print("No identity is held for that token.")
    return 1


def command_access_log(args) -> int:
    from robopii.storage import get_protected_vault, initialise_databases

    initialise_databases()
    entries = get_protected_vault().access_history(token=args.token, limit=args.limit)

    if not entries:
        print("The access log is empty.")
        return 0

    for entry in entries:
        found = "found" if entry["was_found"] else "not found"
        print(
            f"{entry['accessed_at']}  {entry['action']:<7} {entry['token']}  "
            f"by {entry['actor']} ({entry['reason']}) - {found}"
        )

    return 0


def command_audit(args) -> int:
    from robopii.pipeline import audit_primary_store

    report = audit_primary_store()

    print(f"Records checked:    {report.records_checked}")
    print(f"Identities checked: {report.identities_checked}")

    if report.passed:
        print("PASS: no raw PII found in the primary store.")
        return 0

    print("FAIL:")

    for problem in report.problems:
        print(f"  - {problem}")

    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robopii",
        description="RoboPII: context-preserving PII scrubbing for robot data.",
    )
    parser.add_argument(
        "--config",
        help="Settings file to use instead of config/settings.yaml.",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    process = commands.add_parser(
        "process",
        help="Scrub an image, video, audio file or transcript and store it.",
    )
    process.add_argument("path", nargs="?", help="Input file.")
    process.add_argument("--text", help="Process this transcript text instead of a file.")
    process.set_defaults(handler=command_process)

    records = commands.add_parser("records", help="List stored scrubbed records.")
    records.add_argument("--limit", type=int, default=10)
    records.set_defaults(handler=command_records)

    context = commands.add_parser(
        "context",
        help="Show de-identified history for a token (no identity revealed).",
    )
    context.add_argument("token")
    context.add_argument("--limit", type=int)
    context.set_defaults(handler=command_context)

    recall = commands.add_parser(
        "recall",
        help="Find past context for a value the robot is hearing again.",
    )
    recall.add_argument("--type", required=True, help="PII type, e.g. PERSON, EMAIL, PHONE.")
    recall.add_argument("--value", required=True)
    recall.add_argument("--limit", type=int)
    recall.set_defaults(handler=command_recall)

    reveal = commands.add_parser(
        "reveal",
        help="Reveal the identity behind a token (authorised actors only, logged).",
    )
    reveal.add_argument("token")
    reveal.add_argument("--actor", required=True)
    reveal.add_argument("--reason", required=True)
    reveal.set_defaults(handler=command_reveal)

    forget = commands.add_parser(
        "forget",
        help="Delete the identity behind a token (authorised actors only, logged).",
    )
    forget.add_argument("token")
    forget.add_argument("--actor", required=True)
    forget.add_argument("--reason", required=True)
    forget.set_defaults(handler=command_forget)

    access_log = commands.add_parser("access-log", help="Show vault access history.")
    access_log.add_argument("--token")
    access_log.add_argument("--limit", type=int, default=20)
    access_log.set_defaults(handler=command_access_log)

    audit = commands.add_parser(
        "audit",
        help="Check that the primary store contains no raw PII.",
    )
    audit.set_defaults(handler=command_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        set_config(load_config(args.config))
        apply_storage_settings()
        return args.handler(args)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(
            f"Missing dependency: {error}. Run 'pip install -r requirements.txt'"
            " (the text scrubber also needs"
            " 'python -m spacy download en_core_web_md').",
            file=sys.stderr,
        )
        return 2
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
