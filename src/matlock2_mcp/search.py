from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import sqlite_vec

from .policy import PolicyDecision, SearchPolicy


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
    document_attribute_keys: tuple[str, ...] = ()
    chunk_attribute_keys: tuple[str, ...] = ()

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
        for keys in (self.document_attribute_keys, self.chunk_attribute_keys):
            if not isinstance(keys, tuple) or any(
                not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                for key in keys
            ):
                raise ValueError("Attribute keys must be tuples of identifiers")


class SearchService:
    _DOC_JOIN = (
        " LEFT JOIN content_attributes da ON da.domain='document' AND da.sha256=d.body_sha256 "
    )
    _CHUNK_JOIN = (
        " LEFT JOIN content_attributes ca ON ca.domain='chunk' AND ca.sha256=c.content_sha256 "
    )
    _DOC_POLICY = "matlock_document_policy(d.path,d.body,d.body_sha256,d.metadata_json,da.attributes_json)"
    _CHUNK_POLICY = (
        "matlock_chunk_policy(c.chunk_id,c.source_path,c.content,c.content_sha256,"
        "ca.attributes_json,d.metadata_json,da.attributes_json)"
    )
    _DOC_FIELDS = (
        "d.path AS source_path, d.metadata_json, da.attributes_json AS document_attributes, "
        + _DOC_POLICY + " AS document_policy"
    )
    _CHUNK_FIELDS = (
        "c.chunk_id, c.ordinal, c.content, ca.attributes_json AS chunk_attributes, "
        + _CHUNK_POLICY + " AS chunk_policy, " + _DOC_FIELDS
    )

    def __init__(
        self, config: SearchConfig, embedder: QueryEmbedder | None = None,
        policy: SearchPolicy | None = None,
    ) -> None:
        self.config = config
        self.embedder = embedder
        self.policy = policy

    def _rewrite(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for name, keys in (
            ("document_attribute_filters", self.config.document_attribute_keys),
            ("chunk_attribute_filters", self.config.chunk_attribute_keys),
        ):
            if name in arguments:
                self._validate_attribute_filters(arguments[name], keys, allow_private=False)
        if self.policy is None:
            return arguments
        try:
            result = self.policy.rewrite(tool, dict(arguments))
        except Exception as exc:
            raise SearchError("Search policy query rewrite failed") from exc
        if not isinstance(result, dict) or set(result) != set(arguments):
            raise SearchError("Search policy returned invalid query arguments")
        return result

    def _install_policy(self, db: sqlite3.Connection, policy_failed: list[bool]) -> None:
        documents: dict[str, str] = {}
        chunks: dict[str, str] = {}

        def decision(value: PolicyDecision) -> str:
            if not isinstance(value, PolicyDecision) or value.action not in ("allow", "hide", "null"):
                raise ValueError("Invalid policy decision")
            if any(type(flag) is not bool for flag in (
                value.disclose_metadata, value.disclose_source, value.disclose_attributes
            )):
                raise ValueError("Invalid policy disclosure decision")
            return json.dumps(asdict(value))

        def evaluate(callback: Callable[[dict[str, Any]], PolicyDecision], record: dict[str, Any]) -> str:
            try:
                return decision(callback(record))
            except Exception:
                policy_failed[0] = True
                raise

        def document(path: str, body: str, digest: str, metadata: str, attributes: str | None) -> str:
            if path not in documents:
                record = {
                    "source_file": path, "content": body, "sha256": digest,
                    "metadata": json.loads(metadata),
                    "attributes": json.loads(attributes) if attributes is not None else None,
                }
                documents[path] = (
                    evaluate(self.policy.document, record) if self.policy is not None
                    else decision(PolicyDecision())
                )
            return documents[path]

        def chunk(
            chunk_id: str, path: str, content: str, digest: str, attributes: str | None,
            metadata: str, document_attributes: str | None,
        ) -> str:
            if chunk_id not in chunks:
                record = {
                    "chunk_id": chunk_id, "source_file": path, "content": content,
                    "sha256": digest,
                    "attributes": json.loads(attributes) if attributes is not None else None,
                    "metadata": json.loads(metadata),
                    "document_attributes": (
                        json.loads(document_attributes) if document_attributes is not None else None
                    ),
                }
                chunks[chunk_id] = (
                    evaluate(self.policy.chunk, record) if self.policy is not None
                    else decision(PolicyDecision())
                )
            return chunks[chunk_id]

        db.create_function("matlock_document_policy", 5, document)
        db.create_function("matlock_chunk_policy", 7, chunk)

    @contextmanager
    def _connection(self, vectors: bool = False) -> Iterator[sqlite3.Connection]:
        db: sqlite3.Connection | None = None
        policy_failed = [False]
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
            self._install_policy(db, policy_failed)
            yield db
        except sqlite3.Error as exc:
            if policy_failed[0]:
                raise SearchError("Search policy evaluation failed") from exc
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
    def _filters(cls, filters: dict[str, Any] | None, column: str = "d.metadata_json") -> tuple[str, list[Any]]:
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
                clauses.append(f"json_type({column}, ?) IS {'NOT ' if value else ''}NULL")
                params.append(path)
                continue
            if operation == "is_null":
                if value is not True:
                    raise ValueError("is_null requires true")
                clauses.append(f"json_type({column}, ?) = 'null'")
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
                alternatives.append(f"(json_type({column}, ?) = ? AND json_extract({column}, ?) {operator} ?)")
                params.extend((path, kind, path, item))
            clauses.append("(" + " OR ".join(alternatives) + ")")
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _text(text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("query must contain 1 to 1000 characters")
        return text.strip()

    @classmethod
    def _validate_attribute_filters(
        cls, filters: dict[str, Any] | None, allowed: tuple[str, ...], *, allow_private: bool,
    ) -> None:
        if filters is None:
            return
        if not isinstance(filters, dict):
            raise ValueError("attribute filters must be objects")
        for field in filters:
            cls._field(field)
            if not allow_private and field.split(".")[0] not in allowed:
                raise ValueError("attribute filter field is not exposed")

    def _where(
        self, metadata: dict[str, Any] | None,
        document: dict[str, Any] | None, chunk: dict[str, Any] | None,
        *, chunks: bool,
    ) -> tuple[str, list[Any]]:
        if not chunks and chunk:
            raise ValueError("chunk attribute filters require chunk results")
        for filters, allowed in (
            (document, self.config.document_attribute_keys),
            (chunk, self.config.chunk_attribute_keys),
        ):
            self._validate_attribute_filters(filters, allowed, allow_private=self.policy is not None)
        terms = [f"json_extract({self._DOC_POLICY}, '$.action') <> 'hide'"]
        if chunks:
            terms.append(f"json_extract({self._CHUNK_POLICY}, '$.action') <> 'hide'")
        if metadata:
            terms.append(f"json_extract({self._DOC_POLICY}, '$.disclose_metadata') = 1")
            if chunks:
                terms.append(f"json_extract({self._CHUNK_POLICY}, '$.disclose_metadata') = 1")
        exposed_document_filter = document and any(
            field.split(".")[0] in self.config.document_attribute_keys for field in document
        )
        exposed_chunk_filter = chunk and any(
            field.split(".")[0] in self.config.chunk_attribute_keys for field in chunk
        )
        if exposed_document_filter:
            terms.append(f"json_extract({self._DOC_POLICY}, '$.disclose_attributes') = 1")
            if chunks:
                terms.append(f"json_extract({self._CHUNK_POLICY}, '$.disclose_attributes') = 1")
        if exposed_chunk_filter:
            terms.append(f"json_extract({self._DOC_POLICY}, '$.disclose_attributes') = 1")
            terms.append(f"json_extract({self._CHUNK_POLICY}, '$.disclose_attributes') = 1")
        params: list[Any] = []
        for filters, column in (
            (metadata, "d.metadata_json"), (document, "da.attributes_json"),
            (chunk, "ca.attributes_json"),
        ):
            if not chunks and column == "ca.attributes_json":
                continue
            clause, values = self._filters(filters, column)
            if clause:
                terms.append(clause[5:])
                params.extend(values)
        return " AND " + " AND ".join(terms), params

    @staticmethod
    def _attributes(raw: str | None, keys: tuple[str, ...], disclose: bool) -> dict[str, Any] | None:
        if raw is None or not disclose:
            return None
        attributes = json.loads(raw)
        return {key: attributes[key] for key in keys if key in attributes}

    def _document_result(self, row: sqlite3.Row, chunk_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        document_policy = json.loads(row["document_policy"])
        policies = [document_policy] + ([chunk_policy] if chunk_policy is not None else [])
        return {
            "source_file": row["source_path"] if all(p["disclose_source"] for p in policies) else None,
            "metadata": json.loads(row["metadata_json"]) if all(p["disclose_metadata"] for p in policies) else None,
            "document_attributes": self._attributes(
                row["document_attributes"], self.config.document_attribute_keys,
                all(p["disclose_attributes"] for p in policies),
            ),
        }

    def _result(self, row: sqlite3.Row) -> dict[str, Any]:
        content = row["content"]
        document_policy = json.loads(row["document_policy"])
        chunk_policy = json.loads(row["chunk_policy"])
        hidden_content = "null" in (document_policy["action"], chunk_policy["action"])
        return {
            "chunk_id": row["chunk_id"],
            **self._document_result(row, chunk_policy),
            "chunk_attributes": self._attributes(
                row["chunk_attributes"], self.config.chunk_attribute_keys,
                document_policy["disclose_attributes"] and chunk_policy["disclose_attributes"],
            ),
            "content": None if hidden_content else content[: self.config.max_content_chars],
            "content_truncated": False if hidden_content else len(content) > self.config.max_content_chars,
        }

    def metadata_search(
        self, metadata_filters: dict[str, Any] | None = None, limit: int = 10, offset: int = 0,
        document_attribute_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._rewrite("metadata_search", {
            "metadata_filters": metadata_filters, "limit": limit, "offset": offset,
            "document_attribute_filters": document_attribute_filters,
        })
        limit, offset = self._limit(args["limit"]), self._offset(args["offset"])
        where, params = self._where(args["metadata_filters"], args["document_attribute_filters"], None, chunks=False)
        with self._connection() as db:
            rows = db.execute(
                "SELECT " + self._DOC_FIELDS + " FROM source_documents d " + self._DOC_JOIN + " WHERE 1=1" + where
                + " ORDER BY d.path LIMIT ? OFFSET ?", (*params, limit + 1, offset)
            ).fetchall()
        has_more = len(rows) > limit
        next_offset = offset + limit if has_more and offset + limit <= 10000 else None
        return {"results": [self._document_result(r) for r in rows[:limit]],
                "next_offset": next_offset, "pagination_truncated": has_more and next_offset is None}

    def _fts(
        self, db: sqlite3.Connection, query: str, filters: dict[str, Any] | None,
        document_filters: dict[str, Any] | None, chunk_filters: dict[str, Any] | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        where, params = self._where(filters, document_filters, chunk_filters, chunks=True)
        # A quoted FTS phrase treats input literally, not as FTS operators.
        literal = '"' + self._text(query).replace('"', '""') + '"'
        score = "NULL" if self.policy is not None else "bm25(chunks_fts)"
        order = "c.source_path, c.ordinal, c.chunk_id" if self.policy is not None else (
            "score, c.source_path, c.ordinal, c.chunk_id"
        )
        return db.execute(
            "SELECT " + self._CHUNK_FIELDS + ", "
            + score + " AS score FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
            "JOIN source_documents d ON d.path=c.source_path " + self._DOC_JOIN + self._CHUNK_JOIN
            + " WHERE chunks_fts MATCH ?" + where
            + " ORDER BY " + order + " LIMIT ?",
            (literal, *params, limit),
        ).fetchall()

    def full_text_search(
        self, keyword_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
        document_attribute_filters: dict[str, Any] | None = None,
        chunk_attribute_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._rewrite("full_text_search", {
            "keyword_query": keyword_query, "metadata_filters": metadata_filters, "limit": limit,
            "document_attribute_filters": document_attribute_filters,
            "chunk_attribute_filters": chunk_attribute_filters,
        })
        limit = self._limit(args["limit"])
        with self._connection() as db:
            rows = self._fts(
                db, args["keyword_query"], args["metadata_filters"], args["document_attribute_filters"],
                args["chunk_attribute_filters"], limit + 1,
            )
        return {"results": [{**self._result(row), "score": row["score"]} for row in rows[:limit]],
                "truncated": len(rows) > limit}

    def _check_space(
        self, db: sqlite3.Connection, space: str, where: str, params: list[Any]
    ) -> None:
        cfg = self.config
        if not cfg.embedding_space or space != cfg.embedding_space:
            raise SearchError("Embedding space is not configured or does not match")
        if self.policy is None:
            pairs = db.execute(
                "SELECT DISTINCT embedding_model, embedding_version FROM source_documents"
            ).fetchall()
        else:
            pairs = db.execute(
                "SELECT DISTINCT d.embedding_model, d.embedding_version FROM chunks c "
                "JOIN source_documents d ON d.path=c.source_path " + self._DOC_JOIN + self._CHUNK_JOIN
                + " WHERE 1=1" + where, params,
            ).fetchall()
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

    def _vectors(
        self, db: sqlite3.Connection, vector: list[float], space: str, filters: dict[str, Any] | None,
        document_filters: dict[str, Any] | None, chunk_filters: dict[str, Any] | None, limit: int,
    ) -> list[sqlite3.Row]:
        where, params = self._where(filters, document_filters, chunk_filters, chunks=True)
        self._check_space(db, space, where, params)
        if not isinstance(vector, list) or len(vector) != self.config.embedding_dimensions or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector
        ) or not any(vector):
            raise ValueError("vector must have the configured dimension, finite values and nonzero norm")
        try:
            packed = sqlite_vec.serialize_float32(vector)
            float32_values = struct.unpack(f"{len(vector)}f", packed)
        except (OverflowError, ValueError, struct.error) as exc:
            raise ValueError("vector values must fit float32") from exc
        if not all(math.isfinite(v) for v in float32_values) or not any(float32_values):
            raise ValueError("vector must remain finite and nonzero in float32")
        rows = db.execute(
            "SELECT " + self._CHUNK_FIELDS + ", "
            "vec_distance_cosine(v.embedding, ?) AS distance FROM vec_chunks v "
            "JOIN chunks c ON c.id=v.rowid JOIN source_documents d ON d.path=c.source_path "
            + self._DOC_JOIN + self._CHUNK_JOIN +
            "WHERE 1=1" + where + " ORDER BY distance, c.source_path, c.ordinal, c.chunk_id LIMIT ?",
            (packed, *params, limit),
        ).fetchall()
        if any(row["distance"] is None or not math.isfinite(row["distance"]) for row in rows):
            raise SearchError("Index contains an invalid cosine distance")
        return rows

    def vector_search(
        self, vector: list[float], embedding_space: str,
        metadata_filters: dict[str, Any] | None = None, limit: int = 10,
        document_attribute_filters: dict[str, Any] | None = None,
        chunk_attribute_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._rewrite("vector_search", {
            "vector": vector, "embedding_space": embedding_space, "metadata_filters": metadata_filters,
            "limit": limit, "document_attribute_filters": document_attribute_filters,
            "chunk_attribute_filters": chunk_attribute_filters,
        })
        limit = self._limit(args["limit"])
        with self._connection(vectors=True) as db:
            rows = self._vectors(
                db, args["vector"], args["embedding_space"], args["metadata_filters"],
                args["document_attribute_filters"], args["chunk_attribute_filters"], limit + 1,
            )
        return {"results": [{**self._result(row), "distance": row["distance"]} for row in rows[:limit]],
                "truncated": len(rows) > limit}

    def semantic_search(
        self, semantic_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
        document_attribute_filters: dict[str, Any] | None = None,
        chunk_attribute_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._rewrite("semantic_search", {
            "semantic_query": semantic_query, "metadata_filters": metadata_filters, "limit": limit,
            "document_attribute_filters": document_attribute_filters,
            "chunk_attribute_filters": chunk_attribute_filters,
        })
        limit = self._limit(args["limit"])
        vector = self._query_vector(args["semantic_query"])
        with self._connection(vectors=True) as db:
            rows = self._vectors(
                db, vector, self.config.embedding_space or "", args["metadata_filters"],
                args["document_attribute_filters"], args["chunk_attribute_filters"], limit + 1,
            )
        return {"results": [{**self._result(row), "distance": row["distance"]} for row in rows[:limit]],
                "truncated": len(rows) > limit}

    def hybrid_knowledge_search(
        self, semantic_query: str, keyword_query: str,
        metadata_filters: dict[str, Any] | None = None, limit: int = 10,
        document_attribute_filters: dict[str, Any] | None = None,
        chunk_attribute_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._rewrite("hybrid_knowledge_search", {
            "semantic_query": semantic_query, "keyword_query": keyword_query,
            "metadata_filters": metadata_filters, "limit": limit,
            "document_attribute_filters": document_attribute_filters,
            "chunk_attribute_filters": chunk_attribute_filters,
        })
        limit = self._limit(args["limit"])
        vector = self._query_vector(args["semantic_query"])
        window = self.config.candidate_limit
        with self._connection(vectors=True) as db:
            fts = self._fts(db, args["keyword_query"], args["metadata_filters"],
                            args["document_attribute_filters"], args["chunk_attribute_filters"], window + 1)
            vectors = self._vectors(db, vector, self.config.embedding_space or "", args["metadata_filters"],
                                    args["document_attribute_filters"], args["chunk_attribute_filters"], window + 1)
        combined: dict[str, dict[str, Any]] = {}
        for name, rows in (("fts", fts[:window]), ("vector", vectors[:window])):
            for rank, row in enumerate(rows, 1):
                result = combined.setdefault(row["chunk_id"], {**self._result(row), "relevance_score": 0.0,
                                                               "_ordinal": row["ordinal"], "_path": row["source_path"]})
                result["relevance_score"] += 1 / (60 + rank)
                result[name + "_rank"] = rank
                result["fts_score" if name == "fts" else "distance"] = row["score" if name == "fts" else "distance"]
        ranked = sorted(combined.values(), key=lambda r: (-r["relevance_score"], r["_path"], r["_ordinal"], r["chunk_id"]))
        for result in ranked:
            del result["_ordinal"]
            del result["_path"]
        return {"results": ranked[:limit], "truncated": len(ranked) > limit or len(fts) > window or len(vectors) > window,
                "candidate_window": window}

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        chunk_id = self._rewrite("get_chunk", {"chunk_id": chunk_id})["chunk_id"]
        if not isinstance(chunk_id, str) or not re.fullmatch(r"[a-f0-9]{24}", chunk_id):
            raise ValueError("chunk_id must be a 24-character hexadecimal identifier")
        where, params = self._where(None, None, None, chunks=True)
        with self._connection() as db:
            row = db.execute(
                "SELECT " + self._CHUNK_FIELDS + " FROM chunks c "
                "JOIN source_documents d ON d.path=c.source_path " + self._DOC_JOIN + self._CHUNK_JOIN
                + " WHERE c.chunk_id=?" + where, (chunk_id, *params)
            ).fetchone()
        return {"result": self._result(row) if row is not None else None}

    def get_distinct_metadata(self, field: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        args = self._rewrite("get_distinct_metadata", {"field": field, "limit": limit, "offset": offset})
        field = args["field"]
        path, limit, offset = self._field(field), self._limit(args["limit"]), self._offset(args["offset"])
        where, params = self._where(None, None, None, chunks=False)
        with self._connection() as db:
            rows = db.execute(
                "SELECT DISTINCT json_type(d.metadata_json, ?) AS kind, json_extract(d.metadata_json, ?) AS value "
                "FROM source_documents d " + self._DOC_JOIN +
                " WHERE json_type(d.metadata_json, ?) IN ('text','integer','real','true','false','null') "
                + where + f" AND json_extract({self._DOC_POLICY}, '$.disclose_metadata') = 1 "
                "ORDER BY kind, value LIMIT ? OFFSET ?", (path, path, path, *params, limit + 1, offset)
            ).fetchall()
        values = [True if r["kind"] == "true" else False if r["kind"] == "false" else r["value"] for r in rows[:limit]]
        has_more = len(rows) > limit
        next_offset = offset + limit if has_more and offset + limit <= 10000 else None
        return {"field": field, "values": values, "next_offset": next_offset,
                "pagination_truncated": has_more and next_offset is None}
