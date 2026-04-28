"""
Matlock application configuration.

Load and validate a config.yaml file into a MatlockConfig instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class DomainValueConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    alias: str | None = None


class TaskAttributeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["date", "time", "domain"]
    alias: str | None = None
    values: dict[str, DomainValueConfig] | None = None

    @model_validator(mode="after")
    def _domain_requires_values(self) -> "TaskAttributeConfig":
        if self.type == "domain" and not self.values:
            raise ValueError("domain-type attribute must define a non-empty 'values' map")
        return self


class ResourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["DIRECTORY", "FILE"]
    path: str


# ---------------------------------------------------------------------------
# Project / super-project models
# ---------------------------------------------------------------------------


class SuperProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    priority: str | None = None


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    super_project_id: str | None = None
    home_file: str | None = None
    priority: str | None = None
    start_date: str | None = None
    due_date: str | None = None
    resources: list[ResourceConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser limit models
# ---------------------------------------------------------------------------


class HeadersConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    header_text_maxlen: int = 200


class TasksConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_text_maxlen: int = 500


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class MatlockConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_directory: Path
    db_path: Path
    output_directory: Path
    ignore_dirs: list[str] = Field(default_factory=list)
    debounce_seconds: int = 5
    headers: HeadersConfig = Field(default_factory=HeadersConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    task_attributes: dict[str, TaskAttributeConfig] = Field(default_factory=dict)
    super_projects: list[SuperProjectConfig] = Field(default_factory=list)
    projects: list[ProjectConfig] = Field(default_factory=list)
    log_path: Path | None = None
    log_max_bytes: int = 10_000_000   # 10 MB per file
    log_backup_count: int = 3

    @model_validator(mode="after")
    def _check_unique_ids_and_references(self) -> "MatlockConfig":
        # Duplicate super_project IDs
        sp_ids = [sp.id for sp in self.super_projects]
        seen: set[str] = set()
        for sid in sp_ids:
            if sid in seen:
                raise ValueError(f"Duplicate super_project id: '{sid}'")
            seen.add(sid)

        # Duplicate project IDs
        proj_ids = [p.id for p in self.projects]
        seen = set()
        for pid in proj_ids:
            if pid in seen:
                raise ValueError(f"Duplicate project id: '{pid}'")
            seen.add(pid)

        # Unknown super_project_id references
        sp_id_set = set(sp_ids)
        for project in self.projects:
            if project.super_project_id is not None:
                if project.super_project_id not in sp_id_set:
                    raise ValueError(
                        f"Unknown super_project_id '{project.super_project_id}'"
                        f" on project '{project.id}'"
                    )

        return self


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> MatlockConfig:
    """Load and validate a config.yaml file.

    Only ``output_directory`` is resolved relative to ``base_directory`` when
    given as a relative path.  ``db_path`` and ``log_path`` are intentionally
    **not** resolved against ``base_directory`` — use absolute paths for those
    so the database and log file can live outside the vault.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML fails schema validation.
    """
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as fh:
        data: dict = yaml.safe_load(fh) or {}

    # Resolve output_directory relative to base_directory when given as a
    # relative path (D1).  db_path and log_path are left as-is so they can
    # live outside the vault.
    base = Path(data.get("base_directory", ""))
    if "output_directory" in data:
        p = Path(data["output_directory"])
        if not p.is_absolute():
            data["output_directory"] = base / p

    return MatlockConfig(**data)


def validate_config_paths(config: MatlockConfig) -> None:
    """Validate filesystem-level constraints on a loaded MatlockConfig.

    Only checks existence of paths — does not create directories or
    test writability (D2, D3, D4).

    Raises:
        ValueError: with a descriptive message for each failed check.
    """
    if not config.base_directory.exists() or not config.base_directory.is_dir():
        raise ValueError(f"base_directory does not exist: {config.base_directory}")

    if not config.db_path.parent.exists():
        raise ValueError(f"db_path parent directory does not exist: {config.db_path.parent}")
