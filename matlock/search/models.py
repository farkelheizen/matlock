from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SEARCH_REQUEST_CONTRACT = "matlock.search.v1"
SEARCH_RESPONSE_CONTRACT = "matlock.search.response.v1"

SearchMode = Literal["hybrid", "fts_only", "vector_only", "metadata_only"]
ProjectMatchMode = Literal["exact", "fuzzy", "vector", "hybrid"]
MetadataOperator = Literal["eq", "neq", "contains", "gt", "gte", "lt", "lte", "in"]
OutputGranularity = Literal["chunk", "file"]


def _normalize_rfc3339(value: datetime | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = value.strip()
        if not text:
            raise ValueError("datetime value cannot be empty")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    normalized = parsed.isoformat()
    if normalized.endswith("+00:00"):
        normalized = normalized[:-6] + "Z"
    return normalized


def _normalize_absolute_path(value: str | Path) -> str:
    if isinstance(value, Path):
        path = value
    else:
        text = str(value).strip()
        if text.startswith("file://"):
            return text
        path = Path(text)
    if not path.is_absolute():
        raise ValueError("absolute_path must be an absolute path or file URI")
    return path.as_uri()


class SearchTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hybrid_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    min_score: float | None = None


class SearchDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: str | None = None
    max: str | None = None

    @field_validator("min", "max", mode="before")
    @classmethod
    def _normalize_datetimes(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_rfc3339(value)


class SearchMetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    operator: MetadataOperator = "eq"
    value: Any

    @model_validator(mode="before")
    @classmethod
    def _normalize_collection_values(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        operator = data.get("operator", "eq")
        value = data.get("value")
        if operator not in {"contains", "in"} or isinstance(value, list):
            return data
        normalized = dict(data)
        normalized["value"] = [value]
        return normalized


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: SearchDateRange | None = None
    modified: SearchDateRange | None = None
    project_id: list[str] | None = None
    super_project_id: list[str] | None = None
    project_match_mode: ProjectMatchMode = "hybrid"
    file_paths: list[str] = Field(default_factory=list)
    metadata: list[SearchMetadataFilter] = Field(default_factory=list)

    @field_validator("project_id", "super_project_id", mode="before")
    @classmethod
    def _normalize_string_or_list(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else None
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None


class SearchOutputOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granularity: OutputGranularity = "chunk"
    surrounding_chunks: int = Field(default=0, ge=0, le=3)
    limit: int = Field(default=10, ge=1)
    offset: int = Field(default=0, ge=0)
    include_content: bool = True


class MatlockSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", title=SEARCH_REQUEST_CONTRACT)

    query: str | None = None
    search_mode: SearchMode = "hybrid"
    tuning: SearchTuning = Field(default_factory=SearchTuning)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    output: SearchOutputOptions = Field(default_factory=SearchOutputOptions)

    @field_validator("query", mode="before")
    @classmethod
    def _normalize_query(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="before")
    @classmethod
    def _implicit_metadata_only_without_query(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        query = normalized.get("query")
        if query is None or not str(query).strip():
            normalized["search_mode"] = "metadata_only"
        return normalized


class SearchScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fts_rank: int | None = None
    vector_similarity: float | None = None
    rrf_score: float | None = None


class SearchChunkDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_index: int = Field(ge=0)
    total_chunks: int = Field(ge=1)
    content: str | None = None
    before: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)


class SearchFileDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_matching_chunks: int = Field(ge=1)
    content: str | None = None


class SearchResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str
    absolute_path: str
    project_id: str | None = None
    super_project_id: str | None = None
    score: float
    score_breakdown: SearchScoreBreakdown = Field(default_factory=SearchScoreBreakdown)
    created: str
    modified: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    chunk_details: SearchChunkDetails | None = None
    file_details: SearchFileDetails | None = None

    @field_validator("absolute_path", mode="before")
    @classmethod
    def _normalize_uri(cls, value: object) -> str:
        return _normalize_absolute_path(value)

    @field_validator("created", "modified", mode="before")
    @classmethod
    def _normalize_timestamps(cls, value: object) -> str:
        return _normalize_rfc3339(value)

    @model_validator(mode="after")
    def _check_detail_shapes(self) -> "SearchResponseResult":
        if self.chunk_details is not None and self.file_details is not None:
            raise ValueError("result cannot include both chunk_details and file_details")
        return self


class SearchResponseStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_matches: int = Field(ge=0)
    returned_matches: int = Field(ge=0)
    query_time_ms: float = Field(ge=0)
    search_mode_executed: SearchMode


class SearchError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = None


class MatlockSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", title=SEARCH_RESPONSE_CONTRACT)

    status: Literal["success", "error"]
    stats: SearchResponseStats
    results: list[SearchResponseResult] = Field(default_factory=list)
    error: SearchError | None = None

    @model_validator(mode="after")
    def _validate_error_payload(self) -> "MatlockSearchResponse":
        if self.status == "error" and self.error is None:
            raise ValueError("error responses must include an error payload")
        if self.status == "success" and self.error is not None:
            raise ValueError("success responses cannot include an error payload")
        return self