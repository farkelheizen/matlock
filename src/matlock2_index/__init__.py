"""Shared SQLite index contract for Matlock2 components."""

from .schema import (
    CONTENT_ATTRIBUTES,
    CHUNKS,
    CHUNKS_FTS,
    DATABASE_METADATA,
    SCHEMA_VERSION,
    SOURCE_DOCUMENTS,
    VECTOR_CHUNKS,
    IndexSchemaError,
    connect_index,
    validate_index_schema,
)

__all__ = [
    "CONTENT_ATTRIBUTES",
    "CHUNKS",
    "CHUNKS_FTS",
    "DATABASE_METADATA",
    "SCHEMA_VERSION",
    "SOURCE_DOCUMENTS",
    "VECTOR_CHUNKS",
    "IndexSchemaError",
    "connect_index",
    "validate_index_schema",
]
