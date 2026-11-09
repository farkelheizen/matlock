from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

import sqlite_vec


class SearchError(RuntimeError):
    """The index or search provider cannot satisfy the request."""


class QueryEmbedder(Protocol):
    model_name: str
    version: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class SearchConfig:
    database_path: Path
    busy_timeout_ms: int = 5000
    max_limit: int = 50
    candidate_limit: int = 100
    max_content_chars: int = 8000
    embedding_space: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dimensions: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        if not 1 <= self.max_limit <= 100 or not self.max_limit <= self.candidate_limit <= 1000:
            raise ValueError("Require 1 <= max_limit <= candidate_limit <= 1000")
        if not 1 <= self.max_content_chars <= 100000:
            raise ValueError("max_content_chars must be between 1 and 100000")
        values = (self.embedding_space, self.embedding_model, self.embedding_version, self.embedding_dimensions)
        if any(value is not None for value in values):
            if not all(values) or not isinstance(self.embedding_dimensions, int) or self.embedding_dimensions < 1:
                raise ValueError("A vector configuration requires space, model, version and dimensions")


class SearchService:
    def __init__(self, config: SearchConfig, embedder: QueryEmbedder | None = None) -> None:
        self.config = config
        self.embedder = embedder

    @contextmanager
    def _connection(self, vectors: bool = False) -> Iterator[sqlite3.Connection]:
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(
                self.config.database_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=self.config.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if vectors:
                db.enable_load_extension(True)
                try:
                    sqlite_vec.load(db)
                finally:
                    db.enable_load_extension(False)
            db.execute("BEGIN")
            metadata = dict(db.execute("SELECT key, value FROM database_metadata").fetchall())
            if metadata.get("schema_version") != "2":
                raise SearchError("Unsupported database schema version")
            if vectors and metadata.get("vector_dimensions") != str(self.config.embedding_dimensions):
                raise SearchError("Database vector dimensions do not match configuration")
            yield db
        except sqlite3.Error as exc:
            raise SearchError("Database search failed; check the index and SQLite configuration") from exc
        finally:
            if db is not None:
                db.close()

    def _limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.config.max_limit:
            raise ValueError(f"limit must be between 1 and {self.config.max_limit}")
        return limit

    @staticmethod
    def _offset(offset: int) -> int:
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise ValueError("offset must be between 0 and 10000")
        return offset

    @staticmethod
    def _field(field: str) -> str:
        if not isinstance(field, str) or len(field) > 128 or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", field
        ):
            raise ValueError("field must contain dot-separated identifiers")
        return "$." + field

    @classmethod
    def _filters(cls, filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
        if filters is None:
            return "", []
        if not isinstance(filters, dict) or len(filters) > 20:
            raise ValueError("metadata_filters must contain at most 20 fields")
        clauses: list[str] = []
        params: list[Any] = []
        for field, value in filters.items():
            path = cls._field(field)
            operation = "eq"
            if isinstance(value, dict):
                if set(value) != {"op", "value"}:
                    raise ValueError("filter objects require op and value")
                operation, value = value["op"], value["value"]
            if operation == "exists":
                if not isinstance(value, bool):
                    raise ValueError("exists requires a boolean value")
                clauses.append(f"json_type(d.metadata_json, ?) IS {'NOT ' if value else ''}NULL")
                params.append(path)
                continue
            if operation == "is_null":
                if value is not True:
                    raise ValueError("is_null requires true")
                clauses.append("json_type(d.metadata_json, ?) = 'null'")
                params.append(path)
                continue
            if operation not in ("eq", "in", "lt", "lte", "gt", "gte"):
                raise ValueError("unsupported metadata filter operator")
            values = value if operation == "in" else [value]
            if not isinstance(values, list) or not 1 <= len(values) <= 20:
                raise ValueError("in requires 1 to 20 scalar values")
            alternatives: list[str] = []
            for item in values:
                if item is None or not isinstance(item, (str, int, float, bool)):
                    raise ValueError("metadata filter values must be non-null scalars")
                if isinstance(item, int) and not isinstance(item, bool) and not -(2**63) <= item < 2**63:
                    raise ValueError("metadata integer is outside SQLite's supported range")
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("metadata filter values must be finite")
                if isinstance(item, str) and len(item) > 1000:
                    raise ValueError("metadata filter value is too long")
                if operation in ("lt", "lte", "gt", "gte") and isinstance(item, bool):
                    raise ValueError("range filters require a string or number")
                kind = "true" if item is True else "false" if item is False else (
                    "text" if isinstance(item, str) else "integer" if isinstance(item, int) else "real"
                )
                operator = {"eq": "=", "in": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[operation]
                alternatives.append(f"(json_type(d.metadata_json, ?) = ? AND json_extract(d.metadata_json, ?) {operator} ?)")
                params.extend((path, kind, path, item))
            clauses.append("(" + " OR ".join(alternatives) + ")")
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _text(text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("query must contain 1 to 1000 characters")
        return text.strip()

    def _result(self, row: sqlite3.Row) -> dict[str, Any]:
        content = row["content"]
        return {
            "chunk_id": row["chunk_id"],
            "source_file": row["source_path"],
            "metadata": json.loads(row["metadata_json"]),
            "content": content[: self.config.max_content_chars],
            "content_truncated": len(content) > self.config.max_content_chars,
        }

    def metadata_search(self, metadata_filters: dict[str, Any] | None = None, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        limit, offset = self._limit(limit), self._offset(offset)
        where, params = self._filters(metadata_filters)
        with self._connection() as db:
            rows = db.execute(
                "SELECT d.path, d.metadata_json FROM source_documents d WHERE 1=1" + where
                + " ORDER BY d.path LIMIT ? OFFSET ?", (*params, limit + 1, offset)
            ).fetchall()
        has_more = len(rows) > limit
        next_offset = offset + limit if has_more and offset + limit <= 10000 else None
        return {"results": [{"source_file": r["path"], "metadata": json.loads(r["metadata_json"])} for r in rows[:limit]],
                "next_offset": next_offset, "pagination_truncated": has_more and next_offset is None}

    def _fts(self, db: sqlite3.Connection, query: str, filters: dict[str, Any] | None, limit: int) -> list[sqlite3.Row]:
        where, params = self._filters(filters)
        # A quoted FTS phrase treats input literally, not as FTS operators.
        literal = '"' + self._text(query).replace('"', '""') + '"'
        return db.execute(
            "SELECT c.chunk_id, c.source_path, c.ordinal, c.content, d.metadata_json, "
            "bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
            "JOIN source_documents d ON d.path=c.source_path WHERE chunks_fts MATCH ?" + where
            + " ORDER BY score, c.source_path, c.ordinal, c.chunk_id LIMIT ?",
            (literal, *params, limit),
        ).fetchall()

    def full_text_search(self, keyword_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = self._fts(db, keyword_query, metadata_filters, limit + 1)
        return {"results": [{**self._result(row), "score": row["score"]} for row in rows[:limit]],
                "truncated": len(rows) > limit}

    def _check_space(self, db: sqlite3.Connection, space: str) -> None:
        cfg = self.config
        if not cfg.embedding_space or space != cfg.embedding_space:
            raise SearchError("Embedding space is not configured or does not match")
        pairs = db.execute("SELECT DISTINCT embedding_model, embedding_version FROM source_documents").fetchall()
        if any((row[0], row[1]) != (cfg.embedding_model, cfg.embedding_version) for row in pairs):
            raise SearchError("Index contains incompatible embedding models or versions")

    def _query_vector(self, query: str) -> list[float]:
        embedder = self.embedder
        cfg = self.config
        if embedder is None or not cfg.embedding_space:
            raise SearchError("A query embedding provider is not configured")
        if (embedder.model_name, embedder.version, embedder.dimensions) != (
            cfg.embedding_model, cfg.embedding_version, cfg.embedding_dimensions
        ):
            raise SearchError("Query provider does not match the configured embedding space")
        text = self._text(query)
        try:
            vectors = embedder.embed([text])
        except Exception as exc:
            raise SearchError("Query embedding failed") from exc
        if len(vectors) != 1:
            raise SearchError("Query provider returned an invalid vector count")
        return vectors[0]

    def _vectors(self, db: sqlite3.Connection, vector: list[float], space: str, filters: dict[str, Any] | None, limit: int) -> list[sqlite3.Row]:
        self._check_space(db, space)
        if not isinstance(vector, list) or len(vector) != self.config.embedding_dimensions or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector
        ) or not any(vector):
            raise ValueError("vector must have the configured dimension, finite values and nonzero norm")
        where, params = self._filters(filters)
        try:
            packed = sqlite_vec.serialize_float32(vector)
            float32_values = struct.unpack(f"{len(vector)}f", packed)
        except (OverflowError, ValueError, struct.error) as exc:
            raise ValueError("vector values must fit float32") from exc
        if not all(math.isfinite(v) for v in float32_values) or not any(float32_values):
            raise ValueError("vector must remain finite and nonzero in float32")
        rows = db.execute(
            "SELECT c.chunk_id, c.source_path, c.ordinal, c.content, d.metadata_json, "
            "vec_distance_cosine(v.embedding, ?) AS distance FROM vec_chunks v "
            "JOIN chunks c ON c.id=v.rowid JOIN source_documents d ON d.path=c.source_path "
            "WHERE 1=1" + where + " ORDER BY distance, c.source_path, c.ordinal, c.chunk_id LIMIT ?",
            (packed, *params, limit),
        ).fetchall()
        if any(row["distance"] is None or not math.isfinite(row["distance"]) for row in rows):
            raise SearchError("Index contains an invalid cosine distance")
        return rows

    def vector_search(self, vector: list[float], embedding_space: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        limit = self._limit(limit)
        with self._connection(vectors=True) as db:
            rows = self._vectors(db, vector, embedding_space, metadata_filters, limit + 1)
        return {"results": [{**self._result(row), "distance": row["distance"]} for row in rows[:limit]],
                "truncated": len(rows) > limit}

    def semantic_search(self, semantic_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        limit = self._limit(limit)
        vector = self._query_vector(semantic_query)
        return self.vector_search(vector, self.config.embedding_space or "", metadata_filters, limit)

    def hybrid_knowledge_search(self, semantic_query: str, keyword_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        limit = self._limit(limit)
        vector = self._query_vector(semantic_query)
        window = self.config.candidate_limit
        with self._connection(vectors=True) as db:
            fts = self._fts(db, keyword_query, metadata_filters, window + 1)
            vectors = self._vectors(db, vector, self.config.embedding_space or "", metadata_filters, window + 1)
        combined: dict[str, dict[str, Any]] = {}
        for name, rows in (("fts", fts[:window]), ("vector", vectors[:window])):
            for rank, row in enumerate(rows, 1):
                result = combined.setdefault(row["chunk_id"], {**self._result(row), "relevance_score": 0.0, "_ordinal": row["ordinal"]})
                result["relevance_score"] += 1 / (60 + rank)
                result[name + "_rank"] = rank
                result["fts_score" if name == "fts" else "distance"] = row["score" if name == "fts" else "distance"]
        ranked = sorted(combined.values(), key=lambda r: (-r["relevance_score"], r["source_file"], r["_ordinal"], r["chunk_id"]))
        for result in ranked:
            del result["_ordinal"]
        return {"results": ranked[:limit], "truncated": len(ranked) > limit or len(fts) > window or len(vectors) > window,
                "candidate_window": window}

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        if not isinstance(chunk_id, str) or not re.fullmatch(r"[a-f0-9]{24}", chunk_id):
            raise ValueError("chunk_id must be a 24-character hexadecimal identifier")
        with self._connection() as db:
            row = db.execute(
                "SELECT c.chunk_id, c.source_path, c.content, d.metadata_json FROM chunks c "
                "JOIN source_documents d ON d.path=c.source_path WHERE c.chunk_id=?", (chunk_id,)
            ).fetchone()
        return {"result": self._result(row) if row is not None else None}

    def get_distinct_metadata(self, field: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        path, limit, offset = self._field(field), self._limit(limit), self._offset(offset)
        with self._connection() as db:
            rows = db.execute(
                "SELECT DISTINCT json_type(metadata_json, ?) AS kind, json_extract(metadata_json, ?) AS value "
                "FROM source_documents WHERE json_type(metadata_json, ?) IN ('text','integer','real','true','false','null') "
                "ORDER BY kind, value LIMIT ? OFFSET ?", (path, path, path, limit + 1, offset)
            ).fetchall()
        values = [True if r["kind"] == "true" else False if r["kind"] == "false" else r["value"] for r in rows[:limit]]
        has_more = len(rows) > limit
        next_offset = offset + limit if has_more and offset + limit <= 10000 else None
        return {"field": field, "values": values, "next_offset": next_offset,
                "pagination_truncated": has_more and next_offset is None}
