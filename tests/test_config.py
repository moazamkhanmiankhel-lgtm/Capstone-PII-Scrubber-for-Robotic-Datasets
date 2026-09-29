"""Tests for loading shared settings."""

from pathlib import Path

import pytest

from robopii import config, storage
from robopii.config import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    ConfigError,
    Settings,
    apply_storage_settings,
    load_config,
    settings_from_dict,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("ROBOPII_CONFIG", raising=False)
    monkeypatch.delenv("ROBOPII_DATA_DIR", raising=False)


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_project_settings_file_loads():
    settings = load_config(DEFAULT_CONFIG_PATH)

    assert settings.source_path == DEFAULT_CONFIG_PATH
    assert settings.storage.data_dir == PROJECT_ROOT / "data"
    assert settings.pipeline.output_dir == PROJECT_ROOT / "output" / "scrubbed"
    assert settings.pipeline.keep_original_frames is False
    assert "operator" in settings.retrieval.authorised_actors


def test_empty_file_gives_defaults(tmp_path):
    settings = load_config(write(tmp_path, ""))

    assert settings.pipeline == Settings().pipeline
    assert settings.retrieval == Settings().retrieval


def test_missing_default_file_gives_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    assert load_config() == Settings()


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml")


def test_environment_variable_selects_file(monkeypatch, tmp_path):
    path = write(tmp_path, "retrieval:\n  max_records: 3\n")
    monkeypatch.setenv("ROBOPII_CONFIG", str(path))

    assert load_config().retrieval.max_records == 3


def test_relative_paths_resolve_against_project_root():
    settings = settings_from_dict({"pipeline": {"output_dir": "somewhere/else"}})

    assert settings.pipeline.output_dir == PROJECT_ROOT / "somewhere" / "else"


def test_absolute_paths_are_kept(tmp_path):
    settings = settings_from_dict({"storage": {"data_dir": str(tmp_path)}})

    assert settings.storage.data_dir == tmp_path


@pytest.mark.parametrize(
    "raw",
    [
        {"pipline": {}},
        {"pipeline": {"keep_originals": True}},
        {"pipeline": {"keep_original_frames": "no"}},
        {"retrieval": {"max_records": 0}},
        {"retrieval": {"max_records": "ten"}},
        {"retrieval": {"authorised_actors": "operator"}},
        {"pipeline": ["not", "a", "mapping"]},
        ["not", "a", "mapping"],
    ],
)
def test_invalid_settings_are_rejected(raw):
    with pytest.raises(ConfigError):
        settings_from_dict(raw)


def test_malformed_yaml_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "pipeline: [unclosed"))


def test_apply_storage_settings_moves_databases(tmp_path):
    settings = settings_from_dict({"storage": {"data_dir": str(tmp_path / "db")}})

    try:
        apply_storage_settings(settings)
        assert storage.resolve_data_dir() == tmp_path / "db"
    finally:
        storage.configure_storage()


def test_environment_data_dir_wins_over_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ROBOPII_DATA_DIR", str(tmp_path / "from_env"))
    settings = settings_from_dict({"storage": {"data_dir": str(tmp_path / "from_file")}})

    try:
        apply_storage_settings(settings)
        assert storage.resolve_data_dir() == tmp_path / "from_env"
    finally:
        storage.configure_storage()
