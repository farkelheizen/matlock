from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from matlock.config import MatlockConfig
from matlock.search.embedding import EmbeddingProvider, create_embedding_provider
from matlock.search.models import (
    MatlockSearchRequest,
    MatlockSearchResponse,
    SearchResponseResult,
    SearchResponseStats,
    SearchScoreBreakdown,
)
from matlock.search.ranking import cosine_similarity, hybrid_rrf_score
from matlock.search.sql_filters import build_file_filter_clause


@dataclass(slots=True)
class ChunkMatch:
    file_path: str
    chunk_id: str
    chunk_index: int
    content: str
    project_id: str | None
    super_project_id: str | None
    frontmatter: dict[str, Any]
    created: str
    modified: str
    has_secrets: bool | None
    secret_detection_error: str | None
    score: float
    score_breakdown: SearchScoreBreakdown


class SearchQueryEngine:
    """Execute Matlock search requests against the local SQLite index."""

    def __init__(
        self,
        config: MatlockConfig,
        conn: sqlite3.Connection,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._config = config
        self._conn = conn
        self._embedding_provider = embedding_provider

    def execute(
        self,
        request: MatlockSearchRequest | dict[str, Any],
    ) -> MatlockSearchResponse:
        """Run one search request and return a validated response payload."""

        normalized_request = (
            request
            if isinstance(request, MatlockSearchRequest)
            else MatlockSearchRequest.model_validate(request)
        )
        started = perf_counter()

        if normalized_request.search_mode == "metadata_only":
            response = self._execute_metadata_only(normalized_request)
        elif normalized_request.search_mode == "fts_only":
            response = self._execute_fts_only(normalized_request)
        elif normalized_request.search_mode == "vector_only":
            response = self._execute_vector_only(normalized_request)
        else:
            response = self._execute_hybrid(normalized_request)

        elapsed_ms = (perf_counter() - started) * 1000.0
        response.stats.query_time_ms = round(elapsed_ms, 3)
        return response

    def _execute_metadata_only(
        self,
        request: MatlockSearchRequest,
    ) -> MatlockSearchResponse:
        if request.output.granularity == "file":
            return self._metadata_file_response(request)

        rows = self._fetch_all_chunks(request)
        matches = [
            self._row_to_chunk_match(
                row,
                score=1.0,
                score_breakdown=SearchScoreBreakdown(),
            )
            for row in rows
        ]
        return self._build_chunk_response(matches, request, search_mode="metadata_only")

    def _execute_fts_only(
        self,
        request: MatlockSearchRequest,
    ) -> MatlockSearchResponse:
        rows = self._fetch_fts_rows(request)
        matches: list[ChunkMatch] = []
        for rank, row in enumerate(rows, start=1):
            score = 1.0 / rank
            if request.tuning.min_score is not None and score < request.tuning.min_score:
                continue
            matches.append(
                self._row_to_chunk_match(
                    row,
                    score=score,
                    score_breakdown=SearchScoreBreakdown(fts_rank=rank),
                )
            )
        return self._build_response(matches, request, search_mode="fts_only")

    def _execute_vector_only(
        self,
        request: MatlockSearchRequest,
    ) -> MatlockSearchResponse:
        rows = self._fetch_vector_rows(request)
        query_vector = self._embed_query(request.query)
        scored_rows = [
            (
                cosine_similarity(query_vector, _decode_embedding(row["embedding"])),
                row,
            )
            for row in rows
        ]
        scored_rows.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))

        matches: list[ChunkMatch] = []
        for similarity, row in scored_rows:
            if request.tuning.min_score is not None and similarity < request.tuning.min_score:
                continue
            matches.append(
                self._row_to_chunk_match(
                    row,
                    score=similarity,
                    score_breakdown=SearchScoreBreakdown(vector_similarity=round(similarity, 6)),
                )
            )
        return self._build_response(matches, request, search_mode="vector_only")

    def _execute_hybrid(
        self,
        request: MatlockSearchRequest,
    ) -> MatlockSearchResponse:
        fts_rows = self._fetch_fts_rows(request)
        vector_rows = self._fetch_vector_rows(request)

        fts_ranks: dict[str, int] = {
            row["chunk_id"]: rank for rank, row in enumerate(fts_rows, start=1)
        }

        query_vector = self._embed_query(request.query)
        vector_scores: list[tuple[float, sqlite3.Row]] = [
            (
                cosine_similarity(query_vector, _decode_embedding(row["embedding"])),
                row,
            )
            for row in vector_rows
        ]
        vector_scores.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        vector_ranks: dict[str, int] = {
            row["chunk_id"]: rank
            for rank, (_, row) in enumerate(vector_scores, start=1)
        }
        vector_similarity_map = {
            row["chunk_id"]: similarity for similarity, row in vector_scores
        }

        row_by_chunk_id: dict[str, sqlite3.Row] = {
            row["chunk_id"]: row for row in fts_rows
        }
        row_by_chunk_id.update({row["chunk_id"]: row for _, row in vector_scores})

        matches: list[ChunkMatch] = []
        for chunk_id, row in row_by_chunk_id.items():
            fts_rank = fts_ranks.get(chunk_id)
            vector_rank = vector_ranks.get(chunk_id)
            score = hybrid_rrf_score(
                fts_rank=fts_rank,
                vector_rank=vector_rank,
                alpha=request.tuning.hybrid_alpha,
            )
            if request.tuning.min_score is not None and score < request.tuning.min_score:
                continue
            matches.append(
                self._row_to_chunk_match(
                    row,
                    score=score,
                    score_breakdown=SearchScoreBreakdown(
                        fts_rank=fts_rank,
                        vector_similarity=(
                            round(vector_similarity_map[chunk_id], 6)
                            if chunk_id in vector_similarity_map
                            else None
                        ),
                        rrf_score=round(score, 6),
                    ),
                )
            )
        matches.sort(key=lambda match: (-match.score, match.chunk_id))
        return self._build_response(matches, request, search_mode="hybrid")

    def _build_response(
        self,
        matches: list[ChunkMatch],
        request: MatlockSearchRequest,
        *,
        search_mode: str,
    ) -> MatlockSearchResponse:
        if request.output.granularity == "file":
            return self._build_file_response(matches, request, search_mode=search_mode)
        return self._build_chunk_response(matches, request, search_mode=search_mode)

    def _build_chunk_response(
        self,
        matches: list[ChunkMatch],
        request: MatlockSearchRequest,
        *,
        search_mode: str,
    ) -> MatlockSearchResponse:
        total_matches = len(matches)
        paginated_matches = self._paginate(matches, request.output.offset, request.output.limit)
        chunk_cache = self._chunk_cache_for_files(
            {match.file_path for match in paginated_matches}
        )

        results = [
            SearchResponseResult(
                file_path=match.file_path,
                absolute_path=(self._config.base_directory / match.file_path),
                project_id=match.project_id,
                super_project_id=match.super_project_id,
                score=round(match.score, 6),
                score_breakdown=match.score_breakdown,
                created=match.created,
                modified=match.modified,
                frontmatter=match.frontmatter,
                has_secrets=match.has_secrets,
                secret_detection_error=match.secret_detection_error,
                chunk_details={
                    "chunk_id": match.chunk_id,
                    "chunk_index": match.chunk_index,
                    "total_chunks": len(chunk_cache.get(match.file_path, [])) or 1,
                    "content": match.content if request.output.include_content else None,
                    "before": self._neighbor_texts(
                        chunk_cache.get(match.file_path, []),
                        match.chunk_index,
                        before=True,
                        count=request.output.surrounding_chunks,
                        include_content=request.output.include_content,
                    ),
                    "after": self._neighbor_texts(
                        chunk_cache.get(match.file_path, []),
                        match.chunk_index,
                        before=False,
                        count=request.output.surrounding_chunks,
                        include_content=request.output.include_content,
                    ),
                },
            )
            for match in paginated_matches
        ]

        return MatlockSearchResponse(
            status="success",
            stats=SearchResponseStats(
                total_matches=total_matches,
                returned_matches=len(results),
                query_time_ms=0.0,
                search_mode_executed=search_mode,
            ),
            results=results,
        )

    def _build_file_response(
        self,
        matches: list[ChunkMatch],
        request: MatlockSearchRequest,
        *,
        search_mode: str,
    ) -> MatlockSearchResponse:
        grouped: dict[str, list[ChunkMatch]] = defaultdict(list)
        for match in matches:
            grouped[match.file_path].append(match)

        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: (-max(match.score for match in item[1]), item[0]),
        )
        total_matches = len(ordered_groups)
        paginated_groups = self._paginate(ordered_groups, request.output.offset, request.output.limit)

        results = []
        for file_path, file_matches in paginated_groups:
            best_match = max(file_matches, key=lambda match: (match.score, -match.chunk_index))
            results.append(
                SearchResponseResult(
                    file_path=file_path,
                    absolute_path=(self._config.base_directory / file_path),
                    project_id=best_match.project_id,
                    super_project_id=best_match.super_project_id,
                    score=round(best_match.score, 6),
                    score_breakdown=best_match.score_breakdown,
                    created=best_match.created,
                    modified=best_match.modified,
                    frontmatter=best_match.frontmatter,
                    has_secrets=best_match.has_secrets,
                    secret_detection_error=best_match.secret_detection_error,
                    file_details={
                        "total_matching_chunks": len(file_matches),
                        "content": self._file_content(
                            file_path,
                            include_content=request.output.include_content,
                        ),
                    },
                )
            )

        return MatlockSearchResponse(
            status="success",
            stats=SearchResponseStats(
                total_matches=total_matches,
                returned_matches=len(results),
                query_time_ms=0.0,
                search_mode_executed=search_mode,
            ),
            results=results,
        )

    def _metadata_file_response(
        self,
        request: MatlockSearchRequest,
    ) -> MatlockSearchResponse:
        filter_clause, params = build_file_filter_clause(request.filters)
        sql = (
            "SELECT f.file_path, f.created, f.modified, f.modified_date, f.meta_data,"
            "       f.has_secrets, f.secret_detection_error,"
            "       (SELECT fp.project_id FROM file_project fp"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS project_id,"
            "       (SELECT p.super_project_id FROM file_project fp"
            "         JOIN project p ON p.project_id = fp.project_id"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS super_project_id,"
            "       (SELECT COUNT(*) FROM search_chunks sc WHERE sc.file_id = f.file_path) AS total_chunks"
            "  FROM file f"
            " WHERE f.deleted = 0 AND f.is_generated = 0"
        )
        if filter_clause:
            sql += f" AND {filter_clause}"
        sql += " ORDER BY f.file_path"
        rows = self._conn.execute(sql, params).fetchall()

        total_matches = len(rows)
        paginated_rows = self._paginate(rows, request.output.offset, request.output.limit)
        results = [
            SearchResponseResult(
                file_path=row["file_path"],
                absolute_path=(self._config.base_directory / row["file_path"]),
                project_id=row["project_id"],
                super_project_id=row["super_project_id"],
                score=1.0,
                score_breakdown=SearchScoreBreakdown(),
                created=_normalize_db_timestamp(row["created"]),
                modified=_normalize_db_timestamp(row["modified"]),
                frontmatter=_load_frontmatter(row["meta_data"]),
                has_secrets=_normalize_nullable_bool(row["has_secrets"]),
                secret_detection_error=row["secret_detection_error"],
                file_details={
                    "total_matching_chunks": max(int(row["total_chunks"] or 0), 1),
                    "content": self._file_content(
                        row["file_path"],
                        include_content=request.output.include_content,
                    ),
                },
            )
            for row in paginated_rows
        ]

        return MatlockSearchResponse(
            status="success",
            stats=SearchResponseStats(
                total_matches=total_matches,
                returned_matches=len(results),
                query_time_ms=0.0,
                search_mode_executed="metadata_only",
            ),
            results=results,
        )

    def _fetch_all_chunks(self, request: MatlockSearchRequest) -> list[sqlite3.Row]:
        return self._conn.execute(*self._chunk_query(request.filters)).fetchall()

    def _fetch_fts_rows(self, request: MatlockSearchRequest) -> list[sqlite3.Row]:
        if not request.query:
            return []
        filter_clause, params = build_file_filter_clause(request.filters)
        sql = (
            "SELECT sc.chunk_id, sc.file_id AS file_path, sc.chunk_index, sc.content,"
            "       f.created, f.modified, f.modified_date, f.meta_data,"
            "       f.has_secrets, f.secret_detection_error,"
            "       (SELECT fp.project_id FROM file_project fp"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS project_id,"
            "       (SELECT p.super_project_id FROM file_project fp"
            "         JOIN project p ON p.project_id = fp.project_id"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS super_project_id,"
            "       bm25(search_fts) AS fts_score"
            "  FROM search_fts"
            "  JOIN search_chunks sc ON sc.search_rowid = search_fts.rowid"
            "  JOIN file f ON f.file_path = sc.file_id"
            " WHERE f.deleted = 0 AND f.is_generated = 0"
            "   AND search_fts MATCH ?"
        )
        query_params: list[Any] = [request.query]
        if filter_clause:
            sql += f" AND {filter_clause}"
            query_params.extend(params)
        sql += " ORDER BY fts_score ASC, sc.chunk_id ASC"
        return self._conn.execute(sql, query_params).fetchall()

    def _fetch_vector_rows(self, request: MatlockSearchRequest) -> list[sqlite3.Row]:
        sql, params = self._chunk_query(request.filters, include_embedding=True)
        return self._conn.execute(sql, params).fetchall()

    def _chunk_query(
        self,
        filters,
        *,
        include_embedding: bool = False,
    ) -> tuple[str, list[Any]]:
        filter_clause, params = build_file_filter_clause(filters)
        select_prefix = (
            "SELECT sc.chunk_id, sc.file_id AS file_path, sc.chunk_index, sc.content,"
            "       f.created, f.modified, f.modified_date, f.meta_data,"
            "       f.has_secrets, f.secret_detection_error,"
            "       (SELECT fp.project_id FROM file_project fp"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS project_id,"
            "       (SELECT p.super_project_id FROM file_project fp"
            "         JOIN project p ON p.project_id = fp.project_id"
            "         WHERE fp.file_path = f.file_path ORDER BY fp.project_id LIMIT 1) AS super_project_id"
        )
        if include_embedding:
            select_prefix += ", sv.embedding"

        sql = (
            select_prefix
            + " FROM search_chunks sc"
            + (" JOIN search_vec sv ON sv.chunk_id = sc.chunk_id" if include_embedding else "")
            + " JOIN file f ON f.file_path = sc.file_id"
            + " WHERE f.deleted = 0 AND f.is_generated = 0"
        )
        if filter_clause:
            sql += f" AND {filter_clause}"
        sql += " ORDER BY sc.file_id ASC, sc.chunk_index ASC"
        return sql, params

    def _row_to_chunk_match(
        self,
        row: sqlite3.Row,
        *,
        score: float,
        score_breakdown: SearchScoreBreakdown,
    ) -> ChunkMatch:
        return ChunkMatch(
            file_path=row["file_path"],
            chunk_id=row["chunk_id"],
            chunk_index=int(row["chunk_index"]),
            content=row["content"],
            project_id=row["project_id"],
            super_project_id=row["super_project_id"],
            frontmatter=_load_frontmatter(row["meta_data"]),
            created=_normalize_db_timestamp(row["created"]),
            modified=_normalize_db_timestamp(row["modified"]),
            has_secrets=_normalize_nullable_bool(row["has_secrets"]),
            secret_detection_error=row["secret_detection_error"],
            score=score,
            score_breakdown=score_breakdown,
        )

    def _embed_query(self, query: str | None) -> list[float]:
        if not query:
            raise ValueError("query is required for vector and hybrid search modes")
        provider = self._embedding_provider
        if provider is None:
            provider = create_embedding_provider(self._config.search.embedding)
            self._embedding_provider = provider
        return provider.embed([query])[0]

    def _chunk_cache_for_files(
        self,
        file_paths: set[str],
    ) -> dict[str, list[sqlite3.Row]]:
        if not file_paths:
            return {}
        placeholders = ", ".join("?" for _ in file_paths)
        rows = self._conn.execute(
            "SELECT file_id, chunk_index, content"
            " FROM search_chunks"
            f" WHERE file_id IN ({placeholders})"
            " ORDER BY file_id, chunk_index",
            list(file_paths),
        ).fetchall()
        cache: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            cache[row["file_id"]].append(row)
        return cache

    def _neighbor_texts(
        self,
        rows: list[sqlite3.Row],
        chunk_index: int,
        *,
        before: bool,
        count: int,
        include_content: bool,
    ) -> list[str]:
        if count <= 0 or not include_content:
            return []
        if before:
            start = max(chunk_index - count, 0)
            return [row["content"] for row in rows[start:chunk_index]]
        end = chunk_index + 1 + count
        return [row["content"] for row in rows[chunk_index + 1 : end]]

    def _file_content(self, file_path: str, *, include_content: bool) -> str:
        if not include_content:
            return ""
        abs_path = self._config.base_directory / file_path
        try:
            return abs_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _paginate(self, values: list[Any], offset: int, limit: int) -> list[Any]:
        return values[offset : offset + limit]


def execute_search(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    request: MatlockSearchRequest | dict[str, Any],
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> MatlockSearchResponse:
    """Convenience wrapper for executing a search request."""

    engine = SearchQueryEngine(
        config,
        conn,
        embedding_provider=embedding_provider,
    )
    return engine.execute(request)


def _load_frontmatter(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_db_timestamp(value: object) -> str:
    if isinstance(value, (int, float)):
        numeric = float(value)
        abs_value = abs(numeric)
        # Support epoch units commonly seen in file metadata stores.
        # - seconds: ~1e9
        # - milliseconds: ~1e12
        # - microseconds: ~1e15
        # - nanoseconds: ~1e18
        if abs_value >= 1e17:
            numeric /= 1_000_000_000.0
        elif abs_value >= 1e14:
            numeric /= 1_000_000.0
        elif abs_value >= 1e11:
            numeric /= 1_000.0
        timestamp = datetime.fromtimestamp(numeric, tz=UTC).replace(microsecond=0)
        return timestamp.isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("unsupported timestamp value: empty string")
        if text.replace(".", "", 1).replace("-", "", 1).isdigit():
            return _normalize_db_timestamp(float(text))
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raise TypeError(f"unsupported timestamp value: {value!r}")


def _normalize_nullable_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _decode_embedding(raw: bytes | str) -> list[float]:
    payload = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    data = json.loads(payload)
    return [float(value) for value in data]


__all__ = ["SearchQueryEngine", "execute_search"]