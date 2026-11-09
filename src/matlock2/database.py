from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import sqlite_vec

from .models import Chunk, IngestConfig
from .markdown import parse_markdown


class DatabaseError(RuntimeError):
    """Raised when the SQLite database cannot meet the configured requirements."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path, config: IngestConfig) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(
                path, timeout=config.busy_timeout_ms / 1000, isolation_level=None
            )
            self.connection.row_factory = sqlite3.Row
            # WAL allows independent readers to continue while ingestion writes.
            journal_mode = self.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise DatabaseError(
                    f"Could not enable SQLite WAL mode (received {journal_mode!r})"
                )
            self.connection.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.enable_load_extension(True)
            sqlite_vec.load(self.connection)
            self.connection.enable_load_extension(False)
        except DatabaseError:
            if hasattr(self, "connection"):
                self.connection.close()
            raise
        except (sqlite3.Error, OSError, AttributeError) as exc:
            if hasattr(self, "connection"):
                self.connection.close()
            raise DatabaseError(
                "Could not load the required sqlite-vec extension. "
                "Check the sqlite-vec installation for this platform."
            ) from exc
        try:
            self._initialize(config.embedding_dimensions, config.vault_path)
        except Exception:
            self.connection.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # Acquire the writer reservation before changing any part of an index.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _initialize(self, dimensions: int, vault_path: Path) -> None:
        connection = self.connection
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS database_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_documents (
                    path TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    processing_fingerprint TEXT NOT NULL,
                    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                    embedding_model TEXT NOT NULL,
                    embedding_version TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT NOT NULL UNIQUE,
                    source_path TEXT NOT NULL REFERENCES source_documents(path) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(source_path, ordinal)
                );
                CREATE INDEX IF NOT EXISTS chunks_source_path_idx
                    ON chunks(source_path);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    content
                );
                CREATE TABLE IF NOT EXISTS ingestion_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error_details TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unresolved'
                        CHECK (status IN ('unresolved', 'resolved', 'superseded')),
                    UNIQUE(source_path, source_hash, stage)
                );
                CREATE INDEX IF NOT EXISTS ingestion_errors_status_idx
                    ON ingestion_errors(status);
                """
            )
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                f"USING vec0(embedding float[{dimensions}])"
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not initialize SQLite schema: {exc}") from exc

        # Existing vectors cannot safely be reused at another configured dimension.
        row = connection.execute(
            "SELECT value FROM database_metadata WHERE key='vector_dimensions'"
        ).fetchone()
        if row is not None and int(row["value"]) != dimensions:
            raise DatabaseError(
                f"Database vector dimension is {row['value']}, configured dimension is "
                f"{dimensions}. Rebuild or migrate the vector database before changing "
                "embedding dimensions."
            )
        schema_row = connection.execute(
            "SELECT value FROM database_metadata WHERE key='schema_version'"
        ).fetchone()
        if schema_row is not None and schema_row["value"] not in {"1", "2"}:
            raise DatabaseError(
                f"Unsupported database schema version: {schema_row['value']}"
            )
        connection.execute(
            "INSERT OR IGNORE INTO database_metadata(key, value) VALUES('schema_version', '1')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO database_metadata(key, value) "
            "VALUES('vector_dimensions', ?)",
            (str(dimensions),),
        )
        if schema_row is None or schema_row["value"] == "1":
            self._migrate_attributes(vault_path)

    def _migrate_attributes(self, vault_path: Path) -> None:
        try:
            with self._transaction():
                # Another ingestion process may have migrated while we waited.
                version = self.connection.execute(
                    "SELECT value FROM database_metadata WHERE key='schema_version'"
                ).fetchone()["value"]
                if version == "2":
                    return
                if version != "1":
                    raise DatabaseError("Unsupported database schema version")
                self.connection.execute("ALTER TABLE source_documents ADD COLUMN body TEXT")
                self.connection.execute("ALTER TABLE source_documents ADD COLUMN body_sha256 TEXT")
                self.connection.execute("ALTER TABLE chunks ADD COLUMN content_sha256 TEXT")
                self.connection.execute(
                    """CREATE TABLE content_attributes (
                        domain TEXT NOT NULL CHECK(domain IN ('document', 'chunk')),
                        sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
                        attributes_json TEXT NOT NULL CHECK(json_valid(attributes_json) AND json_type(attributes_json)='object'),
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(domain, sha256)
                    )"""
                )
                for row in self.connection.execute("SELECT path, sha256 FROM source_documents"):
                    try:
                        source = (vault_path / row["path"]).resolve(strict=True)
                        source.relative_to(vault_path.resolve())
                        raw = source.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                            raise ValueError("source differs from indexed version")
                        body = parse_markdown(row["path"], raw.decode("utf-8-sig")).body
                    except (OSError, ValueError, UnicodeError) as exc:
                        raise DatabaseError(
                            f"Cannot migrate indexed source {row['path']}: "
                            "restore the indexed file version and retry"
                        ) from exc
                    self.connection.execute(
                        "UPDATE source_documents SET body=?, body_sha256=? WHERE path=?",
                        (body, _content_hash(body), row["path"]),
                    )
                for row in self.connection.execute("SELECT id, content FROM chunks"):
                    self.connection.execute(
                        "UPDATE chunks SET content_sha256=? WHERE id=?",
                        (_content_hash(row["content"]), row["id"]),
                    )
                self.connection.execute(
                    "CREATE INDEX source_documents_body_sha256_idx ON source_documents(body_sha256)"
                )
                self.connection.execute(
                    "CREATE INDEX chunks_content_sha256_idx ON chunks(content_sha256)"
                )
                self.connection.execute(
                    "UPDATE database_metadata SET value='2' WHERE key='schema_version'"
                )
        except sqlite3.Error as exc:
            raise DatabaseError("Could not migrate content attributes schema") from exc

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_source(self, relative_path: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM source_documents WHERE path=?", (relative_path,)
        ).fetchone()

    def replace_document(
        self,
        relative_path: str,
        source_hash: str,
        processing_fingerprint: str,
        metadata: dict[str, Any],
        embedding_model: str,
        embedding_version: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        *,
        body: str,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one embedding vector")
        metadata_json = json.dumps(metadata, ensure_ascii=False, allow_nan=False, sort_keys=True)
        now = _now()
        try:
            with self._transaction():
                self.connection.execute(
                    """
                    INSERT INTO source_documents(
                        path, sha256, processing_fingerprint, metadata_json,
                        embedding_model, embedding_version, ingested_at, body, body_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        sha256=excluded.sha256,
                        processing_fingerprint=excluded.processing_fingerprint,
                        metadata_json=excluded.metadata_json,
                        embedding_model=excluded.embedding_model,
                        embedding_version=excluded.embedding_version,
                        ingested_at=excluded.ingested_at,
                        body=excluded.body,
                        body_sha256=excluded.body_sha256
                    """,
                    (
                        relative_path,
                        source_hash,
                        processing_fingerprint,
                        metadata_json,
                        embedding_model,
                        embedding_version,
                        now,
                        body,
                        _content_hash(body),
                    ),
                )
                previous = self.connection.execute(
                    "SELECT id FROM chunks WHERE source_path=?", (relative_path,)
                ).fetchall()
                old_ids = [row["id"] for row in previous]
                if old_ids:
                    # Virtual index rows do not follow the chunk foreign-key cascade.
                    placeholders = ",".join("?" for _ in old_ids)
                    self.connection.execute(
                        f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders})", old_ids
                    )
                    self.connection.execute(
                        f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", old_ids
                    )
                self.connection.execute("DELETE FROM chunks WHERE source_path=?", (relative_path,))
                for chunk, vector in zip(chunks, vectors, strict=True):
                    cursor = self.connection.execute(
                        "INSERT INTO chunks(chunk_id, source_path, ordinal, content, content_sha256) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (chunk.chunk_id, relative_path, chunk.ordinal, chunk.content,
                         _content_hash(chunk.content)),
                    )
                    row_id = cursor.lastrowid
                    if row_id is None:
                        raise DatabaseError("SQLite did not return a chunk row id")
                    # All three tables share the numeric rowid for their joins.
                    self.connection.execute(
                        "INSERT INTO chunks_fts(rowid, chunk_id, content) VALUES (?, ?, ?)",
                        (row_id, chunk.chunk_id, chunk.content),
                    )
                    self.connection.execute(
                        "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                        (row_id, sqlite_vec.serialize_float32(vector)),
                    )
                self._resolve_errors_in_transaction(relative_path, source_hash, now)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Failed to atomically store {relative_path}: {exc}") from exc

    def record_error(
        self,
        relative_path: str,
        source_hash: str,
        stage: str,
        error_details: str,
    ) -> None:
        now = _now()
        details = " ".join(error_details.split())[:1000]
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO ingestion_errors(
                    source_path, source_hash, stage, error_details,
                    attempt_count, first_seen_at, last_attempt_at, status
                ) VALUES (?, ?, ?, ?, 1, ?, ?, 'unresolved')
                ON CONFLICT(source_path, source_hash, stage) DO UPDATE SET
                    error_details=excluded.error_details,
                    attempt_count=ingestion_errors.attempt_count + 1,
                    last_attempt_at=excluded.last_attempt_at,
                    status='unresolved'
                """,
                (relative_path, source_hash, stage, details, now, now),
            )
            self.connection.execute(
                "UPDATE ingestion_errors SET status='superseded', last_attempt_at=? "
                "WHERE source_path=? AND source_hash<>? AND status='unresolved'",
                (now, relative_path, source_hash),
            )
            # A different stage was reached, so an earlier failure for these bytes
            # no longer describes the current attempt.
            self.connection.execute(
                "UPDATE ingestion_errors SET status='resolved', last_attempt_at=? "
                "WHERE source_path=? AND source_hash=? AND stage<>? "
                "AND status='unresolved'",
                (now, relative_path, source_hash, stage),
            )

    def resolve_errors(self, relative_path: str, source_hash: str) -> None:
        with self._transaction():
            self._resolve_errors_in_transaction(relative_path, source_hash, _now())

    def _resolve_errors_in_transaction(
        self, relative_path: str, source_hash: str, timestamp: str
    ) -> None:
        self.connection.execute(
            "UPDATE ingestion_errors SET status='resolved', last_attempt_at=? "
            "WHERE source_path=? AND source_hash=? AND status='unresolved'",
            (timestamp, relative_path, source_hash),
        )
        self.connection.execute(
            "UPDATE ingestion_errors SET status='superseded', last_attempt_at=? "
            "WHERE source_path=? AND source_hash<>? AND status='unresolved'",
            (timestamp, relative_path, source_hash),
        )

    def remove_source(self, relative_path: str) -> bool:
        now = _now()
        with self._transaction():
            rows = self.connection.execute(
                "SELECT id FROM chunks WHERE source_path=?", (relative_path,)
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.connection.execute(
                    f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders})", ids
                )
                self.connection.execute(
                    f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", ids
                )
            cursor = self.connection.execute(
                "DELETE FROM source_documents WHERE path=?", (relative_path,)
            )
            self.connection.execute(
                "UPDATE ingestion_errors SET status='superseded', last_attempt_at=? "
                "WHERE source_path=? AND status='unresolved'",
                (now, relative_path),
            )
            return cursor.rowcount > 0

    def reconcile_sources(self, present_paths: set[str]) -> int:
        indexed = self.connection.execute(
            "SELECT path FROM source_documents"
        ).fetchall()
        missing = [row["path"] for row in indexed if row["path"] not in present_paths]
        for path in missing:
            self.remove_source(path)
        return len(missing)

    def reconcile_errors(self, present_paths: set[str]) -> None:
        if not present_paths:
            with self._transaction():
                self.connection.execute(
                    "UPDATE ingestion_errors SET status='superseded', last_attempt_at=? "
                    "WHERE status='unresolved'",
                    (_now(),),
                )
            return
        placeholders = ",".join("?" for _ in present_paths)
        with self._transaction():
            self.connection.execute(
                "UPDATE ingestion_errors SET status='superseded', last_attempt_at=? "
                f"WHERE status='unresolved' AND source_path NOT IN ({placeholders})",
                (_now(), *sorted(present_paths)),
            )

    def unresolved_error_count(self) -> int:
        row = self.connection.execute(
            "SELECT count(*) AS count FROM ingestion_errors WHERE status='unresolved'"
        ).fetchone()
        return int(row["count"])

    def fts_search(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT c.chunk_id, c.source_path, c.content, d.metadata_json,
                   bm25(chunks_fts) AS relevance_score
            FROM chunks_fts
            JOIN chunks c ON c.id=chunks_fts.rowid
            JOIN source_documents d ON d.path=c.source_path
            WHERE chunks_fts MATCH ?
            ORDER BY relevance_score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

    def get_chunks(self, relative_path: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM chunks WHERE source_path=? ORDER BY ordinal",
            (relative_path,),
        ).fetchall()

    def distinct_metadata_values(self, field: str) -> list[Any]:
        parts = field.split(".")
        # Restrict paths to identifiers instead of accepting arbitrary JSON path syntax.
        if not parts or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) is None for part in parts
        ):
            raise ValueError("Metadata field must contain dot-separated identifiers")
        json_path = "$." + ".".join(parts)
        rows = self.connection.execute(
            """
            SELECT DISTINCT json_extract(metadata_json, ?) AS value
            FROM source_documents
            WHERE json_type(metadata_json, ?) IS NOT NULL
              AND json_type(metadata_json, ?) NOT IN ('object', 'array')
            ORDER BY value
            """,
            (json_path, json_path, json_path),
        ).fetchall()
        return [row["value"] for row in rows]
