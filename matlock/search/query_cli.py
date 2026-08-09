from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from matlock.config import MatlockConfig
from matlock.search.embedding import EmbeddingProviderError
from matlock.search.models import (
    MatlockSearchRequest,
    MatlockSearchResponse,
    SearchError,
    SearchResponseStats,
)
from matlock.search.query_engine import SearchQueryEngine

EXIT_CODE_SUCCESS = 0
EXIT_CODE_INPUT_ERROR = 1
EXIT_CODE_STORAGE_ERROR = 2
EXIT_CODE_EMBEDDING_ERROR = 3

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class SearchCliOutcome:
    response: MatlockSearchResponse
    exit_code: int


def load_request_json(raw_input: str) -> MatlockSearchRequest:
    text = raw_input.strip()
    if not text:
        raise ValueError("stdin request body is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("stdin request body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("search request must be a JSON object")
    return MatlockSearchRequest.model_validate(parsed)


def build_error_response(
    *,
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
    search_mode: str = "metadata_only",
) -> MatlockSearchResponse:
    return MatlockSearchResponse(
        status="error",
        stats=SearchResponseStats(
            total_matches=0,
            returned_matches=0,
            query_time_ms=0.0,
            search_mode_executed=search_mode,
        ),
        results=[],
        error=SearchError(code=error_code, message=message, details=details),
    )


def run_search_request(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    request_data: MatlockSearchRequest | dict[str, Any],
) -> SearchCliOutcome:
    request: MatlockSearchRequest | None = None
    try:
        request = (
            request_data
            if isinstance(request_data, MatlockSearchRequest)
            else MatlockSearchRequest.model_validate(request_data)
        )
        LOGGER.info("Executing search query", extra={"search_mode": request.search_mode})
        response = SearchQueryEngine(config, conn).execute(request)
        LOGGER.info(
            "Search query succeeded",
            extra={
                "search_mode": response.stats.search_mode_executed,
                "returned_matches": response.stats.returned_matches,
            },
        )
        return SearchCliOutcome(response=response, exit_code=EXIT_CODE_SUCCESS)
    except ValidationError as exc:
        LOGGER.debug("Invalid search request: %s", exc)
        return SearchCliOutcome(
            response=build_error_response(
                error_code="input_error",
                message="search request failed schema validation",
                details={"errors": exc.errors()},
                search_mode=_search_mode_for_error(request),
            ),
            exit_code=EXIT_CODE_INPUT_ERROR,
        )
    except ValueError as exc:
        LOGGER.debug("Rejected search request: %s", exc)
        return SearchCliOutcome(
            response=build_error_response(
                error_code="input_error",
                message=str(exc),
                search_mode=_search_mode_for_error(request),
            ),
            exit_code=EXIT_CODE_INPUT_ERROR,
        )
    except EmbeddingProviderError as exc:
        LOGGER.exception("Search query failed due to embedding provider error")
        return SearchCliOutcome(
            response=build_error_response(
                error_code="embedding_error",
                message=str(exc),
                details={"exception_type": type(exc).__name__},
                search_mode=_search_mode_for_error(request),
            ),
            exit_code=EXIT_CODE_EMBEDDING_ERROR,
        )
    except sqlite3.Error as exc:
        LOGGER.exception("Search query failed due to database error")
        return SearchCliOutcome(
            response=build_error_response(
                error_code="database_error",
                message=str(exc),
                details={"exception_type": type(exc).__name__},
                search_mode=_search_mode_for_error(request),
            ),
            exit_code=EXIT_CODE_STORAGE_ERROR,
        )


def format_human_response(response: MatlockSearchResponse) -> str:
    lines = [
        (
            f"Search mode: {response.stats.search_mode_executed} | "
            f"{response.stats.returned_matches}/{response.stats.total_matches} results | "
            f"{response.stats.query_time_ms:.3f} ms"
        )
    ]
    if not response.results:
        lines.append("No matches.")
        return "\n".join(lines)

    for index, result in enumerate(response.results, start=1):
        lines.append(f"{index}. {result.file_path} (score={result.score:.3f})")
        if result.project_id or result.super_project_id:
            metadata_parts = []
            if result.project_id:
                metadata_parts.append(f"project={result.project_id}")
            if result.super_project_id:
                metadata_parts.append(f"super_project={result.super_project_id}")
            lines.append("   " + " | ".join(metadata_parts))

        excerpt = ""
        if result.chunk_details is not None and result.chunk_details.content:
            excerpt = result.chunk_details.content
        elif result.file_details is not None and result.file_details.content:
            excerpt = result.file_details.content
        if excerpt:
            lines.append("   " + _compact_excerpt(excerpt))
    return "\n".join(lines)


def _search_mode_for_error(request: MatlockSearchRequest | None) -> str:
    if request is None:
        return "metadata_only"
    return request.search_mode


def _compact_excerpt(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


__all__ = [
    "EXIT_CODE_EMBEDDING_ERROR",
    "EXIT_CODE_INPUT_ERROR",
    "EXIT_CODE_STORAGE_ERROR",
    "EXIT_CODE_SUCCESS",
    "SearchCliOutcome",
    "build_error_response",
    "format_human_response",
    "load_request_json",
    "run_search_request",
]