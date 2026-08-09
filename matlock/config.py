"""
Matlock application configuration.

Load and validate a config.yaml file into a MatlockConfig instance.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


_VALID_PROJECT_STATUSES = {"Planned", "In Progress", "Complete", "On Hold", "Cancelled"}

_STATUS_CASE_MAP = {s.lower(): s for s in _VALID_PROJECT_STATUSES}

_VALID_PRIORITIES = {"Low", "Medium", "High"}

_PRIORITY_CASE_MAP = {p.lower(): p for p in _VALID_PRIORITIES}


def _normalise_priority(v: object) -> str | None:
    if v is None:
        return None
    normalised = _PRIORITY_CASE_MAP.get(str(v).lower())
    if normalised is None:
        raise ValueError(
            f"Invalid priority {v!r}. "
            f"Must be one of: {sorted(_VALID_PRIORITIES)}"
        )
    return normalised


class SuperProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    priority: str | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v: object) -> str | None:
        return _normalise_priority(v)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    super_project_id: str | None = None
    home_file: str | None = None
    priority: str | None = None
    status: str | None = None
    start_date: str | None = None
    due_date: str | None = None
    resources: list[ResourceConfig] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v: object) -> str | None:
        return _normalise_priority(v)

    @field_validator("status", mode="before")
    @classmethod
    def _normalise_status(cls, v: object) -> str | None:
        if v is None:
            return None
        normalised = _STATUS_CASE_MAP.get(str(v).lower())
        if normalised is None:
            raise ValueError(
                f"Invalid project status {v!r}. "
                f"Must be one of: {sorted(_VALID_PROJECT_STATUSES)}"
            )
        return normalised


# ---------------------------------------------------------------------------
# Parser limit models
# ---------------------------------------------------------------------------


class HeadersConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    header_text_maxlen: int = 200


class TasksConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_text_maxlen: int = 500


class SearchIndexingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    batch_size: int = Field(default=100, ge=1)


class SearchChunkingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["fixed_token", "markdown_header"] = "fixed_token"
    chunk_size: int = Field(default=500, ge=1)
    chunk_overlap: int = Field(default=50, ge=0)
    inject_frontmatter: bool = True
    frontmatter_template: str = (
        "[Project: {db.project_id} | File: {sys.file_name} | Status: {fm.status}]\n\n"
    )

    @model_validator(mode="after")
    def _check_overlap_lt_size(self) -> "SearchChunkingConfig":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("search.chunking.chunk_overlap must be smaller than chunk_size")
        return self


class SearchEmbeddingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["fastembed", "openai-compatible"] = "fastembed"
    model_name: str = "all-MiniLM-L6-v2"
    dimensions: int = Field(default=384, ge=1)
    api_base_url: str | None = None
    api_base_url_env_var: str | None = None
    api_key: str | None = Field(default=None, exclude=True, repr=False)
    api_key_env_var: str | None = "OPENAI_API_KEY"

    @field_validator("api_base_url_env_var", "api_key_env_var", mode="before")
    @classmethod
    def _normalise_env_var_names(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="before")
    @classmethod
    def _apply_explicit_env_overrides(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        updates = dict(data)
        api_base_url_env_var = updates.get("api_base_url_env_var")
        if api_base_url_env_var:
            api_base_url = os.getenv(str(api_base_url_env_var))
            if api_base_url is not None:
                updates["api_base_url"] = api_base_url
        api_key_env_var = updates.get("api_key_env_var", "OPENAI_API_KEY")
        if api_key_env_var:
            api_key = os.getenv(str(api_key_env_var))
            if api_key is not None:
                updates["api_key"] = api_key
        return updates


class SearchConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    indexing: SearchIndexingConfig = Field(default_factory=SearchIndexingConfig)
    chunking: SearchChunkingConfig = Field(default_factory=SearchChunkingConfig)
    embedding: SearchEmbeddingConfig = Field(default_factory=SearchEmbeddingConfig)


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
    dashboard_recent_changes_limit: int = Field(default=10, ge=1)
    headers: HeadersConfig = Field(default_factory=HeadersConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
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
