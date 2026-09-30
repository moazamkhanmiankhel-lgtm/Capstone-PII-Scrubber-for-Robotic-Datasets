"""Shared settings loaded from ``config/settings.yaml``.

Every component that needs a setting should read it from here rather than
opening the YAML file itself, so there is one place that knows the file
format, the defaults and the priority order.

Priority, highest first:

1. Environment variables handled by the owning component
   (for example ``ROBOPII_DATA_DIR`` in ``robopii.storage``).
2. The YAML file named by ``ROBOPII_CONFIG``, or ``config/settings.yaml``.
3. The defaults defined in this module.

Usage::

    from robopii.config import get_config

    settings = get_config()
    settings.pipeline.output_dir
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

CONFIG_ENV_VAR = "ROBOPII_CONFIG"
DATA_DIR_ENV_VAR = "ROBOPII_DATA_DIR"

_cached_settings: "Settings | None" = None


class ConfigError(ValueError):
    """Raised when the settings file is missing values or malformed."""


@dataclass(frozen=True)
class StorageSettings:
    """Where the primary store and protected vault live."""

    data_dir: Path | None = None


@dataclass(frozen=True)
class PipelineSettings:
    """How the processing pipeline behaves."""

    output_dir: Path = PROJECT_ROOT / "output" / "scrubbed"
    keep_original_frames: bool = False
    transcribe_video_audio: bool = True
    link_tokens_in_transcript: bool = True
    verify_no_raw_pii: bool = True
    tokenize_faces: bool = True
    write_scrubbed_mp4: bool = True


@dataclass(frozen=True)
class RetrievalSettings:
    """Who may reveal identities and how much history is returned."""

    authorised_actors: tuple[str, ...] = ("operator", "researcher")
    min_reason_length: int = 5
    max_records: int = 20


@dataclass(frozen=True)
class Settings:
    """All RoboPII settings."""

    storage: StorageSettings = field(default_factory=StorageSettings)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    source_path: Path | None = None


def _resolve_path(value: Any, section: str, key: str) -> Path:
    """Turn a configured path into an absolute path."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ConfigError(f"{section}.{key} must be a non-empty path.")

    path = Path(value).expanduser()

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    return path


def _require_bool(value: Any, section: str, key: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{section}.{key} must be true or false.")

    return value


def _require_positive_int(value: Any, section: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{section}.{key} must be a positive whole number.")

    return value


def _section(raw: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    """Return one section of the file, rejecting unknown keys."""
    section = raw.get(name) or {}

    if not isinstance(section, dict):
        raise ConfigError(f"'{name}' must be a mapping of settings.")

    unknown = sorted(set(section) - allowed)

    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in '{name}': {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )

    return section


def _names(settings_class) -> set[str]:
    return {item.name for item in fields(settings_class)}


def settings_from_dict(
    raw: dict[str, Any] | None,
    source_path: Path | None = None,
) -> Settings:
    """Validate a parsed settings mapping and build ``Settings``."""
    raw = raw or {}

    if not isinstance(raw, dict):
        raise ConfigError("The settings file must contain a mapping.")

    known_sections = {"storage", "pipeline", "retrieval"}
    unknown_sections = sorted(set(raw) - known_sections)

    if unknown_sections:
        raise ConfigError(
            f"Unknown settings section(s): {', '.join(unknown_sections)}."
        )

    storage_raw = _section(raw, "storage", _names(StorageSettings))
    pipeline_raw = _section(raw, "pipeline", _names(PipelineSettings))
    retrieval_raw = _section(raw, "retrieval", _names(RetrievalSettings))

    storage = StorageSettings(
        data_dir=(
            None
            if storage_raw.get("data_dir") is None
            else _resolve_path(storage_raw["data_dir"], "storage", "data_dir")
        )
    )

    pipeline_defaults = PipelineSettings()
    pipeline_values: dict[str, Any] = {}

    if "output_dir" in pipeline_raw:
        pipeline_values["output_dir"] = _resolve_path(
            pipeline_raw["output_dir"], "pipeline", "output_dir"
        )

    for key in (
        "keep_original_frames",
        "transcribe_video_audio",
        "link_tokens_in_transcript",
        "verify_no_raw_pii",
        "tokenize_faces",
        "write_scrubbed_mp4",
    ):
        if key in pipeline_raw:
            pipeline_values[key] = _require_bool(
                pipeline_raw[key], "pipeline", key
            )

    pipeline = PipelineSettings(
        **{
            item.name: pipeline_values.get(
                item.name, getattr(pipeline_defaults, item.name)
            )
            for item in fields(PipelineSettings)
        }
    )

    retrieval_defaults = RetrievalSettings()
    actors = retrieval_raw.get(
        "authorised_actors", retrieval_defaults.authorised_actors
    )

    if isinstance(actors, str) or not isinstance(actors, (list, tuple)):
        raise ConfigError("retrieval.authorised_actors must be a list.")

    cleaned_actors = tuple(
        str(actor).strip() for actor in actors if str(actor).strip()
    )

    retrieval = RetrievalSettings(
        authorised_actors=cleaned_actors,
        min_reason_length=_require_positive_int(
            retrieval_raw.get(
                "min_reason_length", retrieval_defaults.min_reason_length
            ),
            "retrieval",
            "min_reason_length",
        ),
        max_records=_require_positive_int(
            retrieval_raw.get("max_records", retrieval_defaults.max_records),
            "retrieval",
            "max_records",
        ),
    )

    return Settings(
        storage=storage,
        pipeline=pipeline,
        retrieval=retrieval,
        source_path=source_path,
    )


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Return the settings file that should be loaded."""
    if path is not None:
        return Path(path).expanduser()

    configured = os.environ.get(CONFIG_ENV_VAR)

    if configured:
        return Path(configured).expanduser()

    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> Settings:
    """Read and validate a settings file.

    A missing default file gives the built-in defaults. A missing file that
    was asked for explicitly (argument or ``ROBOPII_CONFIG``) is an error,
    because silently using defaults would hide the mistake.
    """
    explicit = path is not None or bool(os.environ.get(CONFIG_ENV_VAR))
    config_path = resolve_config_path(path)

    if not config_path.is_file():
        if explicit:
            raise ConfigError(f"Settings file not found: {config_path}")

        return Settings()

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(
            f"Could not parse settings file {config_path}: {error}"
        ) from error

    return settings_from_dict(raw, source_path=config_path)


def get_config() -> Settings:
    """Return the shared settings, loading them on first use."""
    global _cached_settings

    if _cached_settings is None:
        _cached_settings = load_config()

    return _cached_settings


def set_config(settings: Settings | None) -> None:
    """Replace the shared settings. ``None`` reloads from disk next time."""
    global _cached_settings

    _cached_settings = settings


def apply_storage_settings(settings: Settings | None = None) -> None:
    """Point ``robopii.storage`` at the configured data directory.

    ``ROBOPII_DATA_DIR`` still wins when it is set, so tests and deployments
    can relocate the databases without editing the settings file.
    """
    from robopii.storage import configure_storage

    settings = settings or get_config()

    if os.environ.get(DATA_DIR_ENV_VAR):
        return

    if settings.storage.data_dir is not None:
        configure_storage(data_dir=settings.storage.data_dir)
