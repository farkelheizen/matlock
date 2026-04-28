from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from matlock.config import (
    MatlockConfig,
    load_config,
    validate_config_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, data: dict) -> Path:
    """Write a dict as YAML to tmp_path/config.yaml and return the path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(data), encoding="utf-8")
    return config_file


def _minimal_data(base_dir: str = "/fake/vault") -> dict:
    return {
        "base_directory": base_dir,
        "db_path": "/fake/vault/matlock.db",
        "output_directory": "/fake/vault/_Matlock",
    }


# ---------------------------------------------------------------------------
# Loading: minimal and full configs
# ---------------------------------------------------------------------------


def test_load_minimal_config(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    config = load_config(cfg_file)
    assert isinstance(config, MatlockConfig)
    assert config.base_directory == Path("/fake/vault")


def test_load_full_config(tmp_path: Path) -> None:
    data = {
        "base_directory": "/fake/vault",
        "db_path": "/fake/vault/matlock.db",
        "output_directory": "/fake/vault/_Matlock",
        "ignore_dirs": [".git", ".obsidian"],
        "debounce_seconds": 10,
        "headers": {"header_text_maxlen": 150},
        "tasks": {"task_text_maxlen": 400},
        "task_attributes": {
            "due_date": {"type": "date", "alias": "📅"},
            "priority": {
                "type": "domain",
                "values": {"high": {"alias": "🔴"}, "low": {"alias": "🟢"}},
            },
        },
        "super_projects": [{"id": "sp1", "title": "Super Project 1", "priority": "high"}],
        "projects": [
            {
                "id": "proj1",
                "title": "Project One",
                "super_project_id": "sp1",
                "home_file": "Projects/One.md",
                "priority": "high",
                "resources": [{"type": "DIRECTORY", "path": "Tech/"}],
            }
        ],
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)

    assert config.debounce_seconds == 10
    assert config.ignore_dirs == [".git", ".obsidian"]
    assert config.headers.header_text_maxlen == 150
    assert config.tasks.task_text_maxlen == 400
    assert "due_date" in config.task_attributes
    assert config.task_attributes["due_date"].alias == "📅"
    assert len(config.super_projects) == 1
    assert config.super_projects[0].id == "sp1"
    assert len(config.projects) == 1
    assert config.projects[0].id == "proj1"
    assert config.projects[0].resources[0].type == "DIRECTORY"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_debounce_seconds(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).debounce_seconds == 5


def test_default_ignore_dirs(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).ignore_dirs == []


def test_default_header_maxlen(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).headers.header_text_maxlen == 200


def test_default_task_maxlen(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).tasks.task_text_maxlen == 500


def test_default_super_projects_empty(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).super_projects == []


def test_default_projects_empty(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).projects == []


def test_default_task_attributes_empty(tmp_path: Path) -> None:
    cfg_file = _write_config(tmp_path, _minimal_data())
    assert load_config(cfg_file).task_attributes == {}


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_required_base_directory(tmp_path: Path) -> None:
    data = {"db_path": "/fake/vault/matlock.db", "output_directory": "/fake/vault/_Matlock"}
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_missing_required_db_path(tmp_path: Path) -> None:
    data = {"base_directory": "/fake/vault", "output_directory": "/fake/vault/_Matlock"}
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_missing_required_output_directory(tmp_path: Path) -> None:
    data = {"base_directory": "/fake/vault", "db_path": "/fake/vault/matlock.db"}
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# Structural validation (duplicate IDs and cross-references)
# ---------------------------------------------------------------------------


def test_duplicate_super_project_ids(tmp_path: Path) -> None:
    data = _minimal_data()
    data["super_projects"] = [
        {"id": "sp1", "title": "First"},
        {"id": "sp1", "title": "Duplicate"},
    ]
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError, match="Duplicate super_project id"):
        load_config(cfg_file)


def test_duplicate_project_ids(tmp_path: Path) -> None:
    data = _minimal_data()
    data["projects"] = [
        {"id": "p1", "title": "First"},
        {"id": "p1", "title": "Duplicate"},
    ]
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError, match="Duplicate project id"):
        load_config(cfg_file)


def test_unknown_super_project_id_ref(tmp_path: Path) -> None:
    data = _minimal_data()
    data["super_projects"] = [{"id": "sp1", "title": "SP One"}]
    data["projects"] = [{"id": "p1", "title": "P One", "super_project_id": "nonexistent"}]
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError, match="Unknown super_project_id"):
        load_config(cfg_file)


def test_domain_attribute_missing_values(tmp_path: Path) -> None:
    data = _minimal_data()
    data["task_attributes"] = {"priority": {"type": "domain"}}
    cfg_file = _write_config(tmp_path, data)
    with pytest.raises(ValidationError):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# Relative path resolution (D1)
# ---------------------------------------------------------------------------


def test_relative_db_path_resolved(tmp_path: Path) -> None:
    data = {
        "base_directory": "/fake/vault",
        "db_path": "matlock.db",          # relative
        "output_directory": "/fake/vault/_Matlock",
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)
    assert config.db_path == Path("/fake/vault/matlock.db")
    assert config.db_path.is_absolute()


def test_relative_output_directory_resolved(tmp_path: Path) -> None:
    data = {
        "base_directory": "/fake/vault",
        "db_path": "/fake/vault/matlock.db",
        "output_directory": "_Matlock",   # relative
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)
    assert config.output_directory == Path("/fake/vault/_Matlock")
    assert config.output_directory.is_absolute()


# ---------------------------------------------------------------------------
# validate_config_paths (D2, D4)
# ---------------------------------------------------------------------------


def test_validate_paths_base_dir_missing(tmp_path: Path) -> None:
    data = {
        "base_directory": str(tmp_path / "nonexistent"),
        "db_path": str(tmp_path / "nonexistent" / "matlock.db"),
        "output_directory": str(tmp_path / "nonexistent" / "_Matlock"),
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)
    with pytest.raises(ValueError, match="base_directory does not exist"):
        validate_config_paths(config)


def test_validate_paths_db_parent_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    data = {
        "base_directory": str(vault),
        "db_path": str(vault / "subdir" / "matlock.db"),  # subdir doesn't exist
        "output_directory": str(vault / "_Matlock"),
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)
    with pytest.raises(ValueError, match="db_path parent directory does not exist"):
        validate_config_paths(config)


def test_validate_paths_ok(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    data = {
        "base_directory": str(vault),
        "db_path": str(vault / "matlock.db"),
        "output_directory": str(vault / "_Matlock"),
    }
    cfg_file = _write_config(tmp_path, data)
    config = load_config(cfg_file)
    # Should not raise
    validate_config_paths(config)
