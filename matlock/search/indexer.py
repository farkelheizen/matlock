from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from matlock.config import MatlockConfig
from matlock.db import (
    clear_file_search_state,
    get_files_needing_search_indexing,
    mark_file_search_indexed,
    replace_search_chunks,
    upsert_search_vector,
)
from matlock.parser import parse_front_matter
from matlock.search.chunking import chunk_markdown
from matlock.search.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
    create_embedding_provider,
)
from matlock.search.frontmatter_template import render_frontmatter_template


@dataclasses.dataclass(slots=True)
class SearchIndexingResult:
    indexed_files: int = 0
    excluded_files: int = 0
    failed_files: int = 0
    orphaned_files_purged: int = 0
    chunks_written: int = 0
    vectors_written: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class SearchIndexOverrides:
    force_index: bool = False
    exclude: bool = False


def run_search_indexing(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    indexed_at: str | None = None,
) -> SearchIndexingResult:
    """Build or refresh search chunks and embeddings for eligible files."""

    result = SearchIndexingResult()
    now = indexed_at or dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    active_rows = _get_active_files(conn)
    stale_paths = {
        row["file_path"] for row in get_files_needing_search_indexing(conn)
    }
    force_paths = set() if force else _get_force_index_paths(active_rows)

    candidate_rows = [
        row
        for row in active_rows
        if force or row["file_path"] in stale_paths or row["file_path"] in force_paths
    ]

    provider = embedding_provider
    pending_writes = 0
    for row in candidate_rows:
        file_path = row["file_path"]
        abs_path = config.base_directory / file_path
        try:
            content = abs_path.read_text(encoding="utf-8")
        except OSError:
            result.failed_files += 1
            continue

        try:
            frontmatter, body = parse_front_matter(content)
        except Exception:
            frontmatter = _load_row_metadata(row)
            body = content

        overrides = _get_search_overrides(frontmatter)
        if overrides.exclude:
            clear_file_search_state(conn, file_path)
            mark_file_search_indexed(conn, file_path, now, row["sha256"])
            result.excluded_files += 1
            pending_writes += 1
            if pending_writes >= config.search.indexing.batch_size:
                conn.commit()
                pending_writes = 0
            continue

        chunk_texts = chunk_markdown(
            body,
            strategy=config.search.chunking.strategy,
            chunk_size=config.search.chunking.chunk_size,
            chunk_overlap=config.search.chunking.chunk_overlap,
        )
        prepared_chunks = _prepare_chunk_rows(
            conn,
            row,
            abs_path,
            chunk_texts,
            frontmatter,
            config,
        )

        replace_search_chunks(conn, file_path, prepared_chunks)
        if prepared_chunks:
            provider = provider or create_embedding_provider(config.search.embedding)
            vectors = provider.embed([chunk["content"] for chunk in prepared_chunks])
            if len(vectors) != len(prepared_chunks):
                raise EmbeddingProviderError(
                    f"Embedding provider returned {len(vectors)} vectors for {len(prepared_chunks)} chunks"
                )
            for chunk_row, vector in zip(prepared_chunks, vectors, strict=True):
                if len(vector) != config.search.embedding.dimensions:
                    raise EmbeddingProviderError(
                        f"Embedding for {chunk_row['chunk_id']} has dimension {len(vector)}; "
                        f"expected {config.search.embedding.dimensions}"
                    )
                upsert_search_vector(
                    conn,
                    {
                        "chunk_id": chunk_row["chunk_id"],
                        "embedding": json.dumps(vector).encode("utf-8"),
                        "embedding_model": provider.model_name,
                        "embedding_dim": len(vector),
                    },
                )
            result.vectors_written += len(vectors)

        mark_file_search_indexed(conn, file_path, now, row["sha256"])
        result.indexed_files += 1
        result.chunks_written += len(prepared_chunks)
        pending_writes += 1
        if pending_writes >= config.search.indexing.batch_size:
            conn.commit()
            pending_writes = 0

    purged = _purge_orphaned_search_rows(conn)
    result.orphaned_files_purged = purged
    conn.commit()
    return result


def _get_active_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file WHERE deleted = 0 AND is_generated = 0 ORDER BY file_path"
    ).fetchall()


def _get_force_index_paths(rows: list[sqlite3.Row]) -> set[str]:
    paths: set[str] = set()
    for row in rows:
        overrides = _get_search_overrides(_load_row_metadata(row))
        if overrides.force_index:
            paths.add(row["file_path"])
    return paths


def _load_row_metadata(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["meta_data"]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_search_overrides(frontmatter: dict[str, Any]) -> SearchIndexOverrides:
    matlock_block = frontmatter.get("matlock")
    if not isinstance(matlock_block, dict):
        return SearchIndexOverrides()
    search_block = matlock_block.get("search")
    if not isinstance(search_block, dict):
        return SearchIndexOverrides()
    return SearchIndexOverrides(
        force_index=bool(search_block.get("force_index", False)),
        exclude=bool(search_block.get("exclude", False)),
    )


def _prepare_chunk_rows(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    abs_path: Path,
    chunk_texts: list[str],
    frontmatter: dict[str, Any],
    config: MatlockConfig,
) -> list[dict[str, Any]]:
    template_prefix = ""
    if config.search.chunking.inject_frontmatter:
        template_prefix = render_frontmatter_template(
            config.search.chunking.frontmatter_template,
            frontmatter=frontmatter,
            system={
                "absolute_path": str(abs_path),
                "file_name": abs_path.name,
                "file_path": row["file_path"],
                "modified": row["modified_date"] or row["modified"],
                "created": row["created"],
            },
            database=_get_database_context(conn, row["file_path"]),
        )

    return [
        {
            "chunk_id": f"{row['file_path']}#{index:04d}",
            "chunk_index": index,
            "content": _apply_template_prefix(template_prefix, chunk_text),
        }
        for index, chunk_text in enumerate(chunk_texts)
    ]


def _get_database_context(conn: sqlite3.Connection, file_path: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT fp.project_id, p.super_project_id"
        " FROM file_project fp"
        " LEFT JOIN project p ON p.project_id = fp.project_id"
        " WHERE fp.file_path = ?"
        " ORDER BY fp.project_id"
        " LIMIT 1",
        (file_path,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "project_id": row["project_id"],
        "super_project_id": row["super_project_id"],
    }


def _apply_template_prefix(prefix: str, chunk_text: str) -> str:
    prefix_text = prefix.strip()
    if not prefix_text:
        return chunk_text
    return f"{prefix_text}\n\n{chunk_text}"


def _purge_orphaned_search_rows(conn: sqlite3.Connection) -> int:
    file_ids = [
        row["file_id"]
        for row in conn.execute(
            "SELECT DISTINCT file_id"
            " FROM search_chunks"
            " WHERE file_id NOT IN ("
            "     SELECT file_path FROM file WHERE deleted = 0 AND is_generated = 0"
            " )"
            " ORDER BY file_id"
        ).fetchall()
    ]
    for file_id in file_ids:
        clear_file_search_state(conn, file_id)
    return len(file_ids)


__all__ = [
    "SearchIndexOverrides",
    "SearchIndexingResult",
    "run_search_indexing",
]