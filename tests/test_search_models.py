from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from matlock.search.models import (
    SEARCH_REQUEST_CONTRACT,
    SEARCH_RESPONSE_CONTRACT,
    MatlockSearchRequest,
    MatlockSearchResponse,
)


def test_request_contract_titles_match_plan_contracts() -> None:
    assert MatlockSearchRequest.model_config.get("title") == SEARCH_REQUEST_CONTRACT
    assert MatlockSearchResponse.model_config.get("title") == SEARCH_RESPONSE_CONTRACT


def test_request_defaults_to_hybrid_when_query_present() -> None:
    request = MatlockSearchRequest(query="refactor hot path")

    assert request.query == "refactor hot path"
    assert request.search_mode == "hybrid"
    assert request.output.granularity == "chunk"
    assert request.output.surrounding_chunks == 0
    assert request.output.limit == 10


def test_request_implicit_metadata_only_when_query_missing() -> None:
    request = MatlockSearchRequest(filters={"project_id": "alpha"})

    assert request.query is None
    assert request.search_mode == "metadata_only"
    assert request.filters.project_id == ["alpha"]


def test_request_blank_query_normalizes_to_none() -> None:
    request = MatlockSearchRequest(query="   ", search_mode="vector_only")

    assert request.query is None
    assert request.search_mode == "metadata_only"


def test_request_metadata_collection_operators_normalize_scalars_to_lists() -> None:
    request = MatlockSearchRequest(
        query="status",
        filters={
            "metadata": [
                {"path": "$.tags", "operator": "contains", "value": "refactor"},
                {"path": "$.author", "operator": "in", "value": "alice"},
            ]
        },
    )

    assert request.filters.metadata[0].value == ["refactor"]
    assert request.filters.metadata[1].value == ["alice"]


def test_request_normalizes_string_project_filters() -> None:
    request = MatlockSearchRequest(
        filters={"project_id": "alpha", "super_project_id": ["sp-1", "sp-2"]}
    )

    assert request.filters.project_id == ["alpha"]
    assert request.filters.super_project_id == ["sp-1", "sp-2"]


def test_request_output_rejects_surrounding_chunks_above_limit() -> None:
    with pytest.raises(ValidationError):
        MatlockSearchRequest(query="x", output={"surrounding_chunks": 4})


def test_response_normalizes_paths_and_datetimes() -> None:
    response = MatlockSearchResponse(
        status="success",
        stats={
            "total_matches": 1,
            "returned_matches": 1,
            "query_time_ms": 12.5,
            "search_mode_executed": "hybrid",
        },
        results=[
            {
                "file_path": "Specs/refactor.md",
                "absolute_path": "/tmp/refactor.md",
                "score": 0.75,
                "created": datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
                "modified": "2026-08-09T12:05:00+00:00",
                "frontmatter": {"status": "active"},
                "chunk_details": {
                    "chunk_id": "chunk-1",
                    "chunk_index": 0,
                    "total_chunks": 2,
                    "before": [],
                    "after": ["next chunk"],
                },
            }
        ],
    )

    result = response.results[0]
    assert result.absolute_path == "file:///tmp/refactor.md"
    assert result.created == "2026-08-09T12:00:00Z"
    assert result.modified == "2026-08-09T12:05:00Z"


def test_response_serializes_secret_detection_metadata() -> None:
    response = MatlockSearchResponse(
        status="success",
        stats={
            "total_matches": 1,
            "returned_matches": 1,
            "query_time_ms": 0.0,
            "search_mode_executed": "metadata_only",
        },
        results=[
            {
                "file_path": "Notes/private.md",
                "absolute_path": "/tmp/private.md",
                "score": 1.0,
                "created": "2026-08-09T12:00:00Z",
                "modified": "2026-08-09T12:00:00Z",
                "has_secrets": True,
                "secret_detection_error": "scanner failed",
                "file_details": {"total_matching_chunks": 1},
            }
        ],
    )

    result = response.model_dump()["results"][0]
    assert result["has_secrets"] is True
    assert result["secret_detection_error"] == "scanner failed"


def test_response_rejects_both_detail_blocks() -> None:
    with pytest.raises(ValidationError, match="chunk_details"):
        MatlockSearchResponse(
            status="success",
            stats={
                "total_matches": 1,
                "returned_matches": 1,
                "query_time_ms": 1.0,
                "search_mode_executed": "fts_only",
            },
            results=[
                {
                    "file_path": "Specs/refactor.md",
                    "absolute_path": "/tmp/refactor.md",
                    "score": 1.0,
                    "created": "2026-08-09T12:00:00Z",
                    "modified": "2026-08-09T12:00:00Z",
                    "frontmatter": {},
                    "chunk_details": {
                        "chunk_id": "chunk-1",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                    "file_details": {"total_matching_chunks": 1},
                }
            ],
        )


def test_error_response_requires_error_payload() -> None:
    with pytest.raises(ValidationError, match="error payload"):
        MatlockSearchResponse(
            status="error",
            stats={
                "total_matches": 0,
                "returned_matches": 0,
                "query_time_ms": 0.5,
                "search_mode_executed": "metadata_only",
            },
            results=[],
        )