from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "2"
DATABASE_METADATA = "database_metadata"
SOURCE_DOCUMENTS = "source_documents"
CHUNKS = "chunks"
CHUNKS_FTS = "chunks_fts"
VECTOR_CHUNKS = "vec_chunks"
CONTENT_ATTRIBUTES = "content_attributes"

_REQUIRED_COLUMNS = {
    DATABASE_METADATA: {"key", "value"},
    SOURCE_DOCUMENTS: {
        "path",
        "sha256",
        "processing_fingerprint",
        "metadata_json",
        "embedding_model",
        "embedding_version",
        "ingested_at",
        "body",
        "body_sha256",
    },
    CHUNKS: {"id", "chunk_id", "source_path", "ordinal", "content", "content_sha256"},
    CHUNKS_FTS: {"chunk_id", "content"},
    CONTENT_ATTRIBUTES: {"domain", "sha256", "attributes_json", "updated_at"},
}
_REQUIRED_TABLES = {*_REQUIRED_COLUMNS, VECTOR_CHUNKS}


class IndexSchemaError(RuntimeError):
    """Raised when a database does not satisfy the shared index contract."""


def connect_index(
    path: Path,
    *,
    busy_timeout_ms: int,
    readonly: bool = True,
) -> sqlite3.Connection:
    """Open an index with consistent timeout and read-only settings."""
    if (
        isinstance(busy_timeout_ms, bool)
        or not isinstance(busy_timeout_ms, int)
        or busy_timeout_ms < 1
    ):
        raise ValueError("busy_timeout_ms must be positive")
    if readonly:
        connection = sqlite3.connect(
            path.expanduser().resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(
            path,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
        )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def validate_index_schema(connection: sqlite3.Connection) -> dict[str, str]:
    """Validate the current index contract and return its metadata."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    missing_tables = sorted(_REQUIRED_TABLES - tables)
    if missing_tables:
        raise IndexSchemaError(
            f"Index is missing required table(s): {', '.join(missing_tables)}"
        )

    for table, required in _REQUIRED_COLUMNS.items():
        present = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        missing_columns = sorted(required - present)
        if missing_columns:
            raise IndexSchemaError(
                f"Index table {table} is missing required column(s): "
                f"{', '.join(missing_columns)}"
            )

    metadata = {
        row[0]: row[1]
        for row in connection.execute(f"SELECT key, value FROM {DATABASE_METADATA}")
    }
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise IndexSchemaError(
            f"Unsupported index schema version: {metadata.get('schema_version', 'missing')}"
        )
    return metadata
