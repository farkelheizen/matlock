from __future__ import annotations

import sqlite3

from matlock.config import MatlockConfig
from matlock.search.indexer import SearchIndexingResult, run_search_indexing


def run_search_index_stage(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    batch_size: int | None = None,
    model_name: str | None = None,
) -> SearchIndexingResult:
    """Run search indexing with optional per-invocation config overrides."""
    effective_config = _apply_search_index_overrides(
        config,
        batch_size=batch_size,
        model_name=model_name,
    )
    return run_search_indexing(effective_config, conn, force=force)


def _apply_search_index_overrides(
    config: MatlockConfig,
    *,
    batch_size: int | None = None,
    model_name: str | None = None,
) -> MatlockConfig:
    search_indexing = config.search.indexing
    if batch_size is not None:
        search_indexing = search_indexing.model_copy(update={"batch_size": batch_size})

    search_embedding = config.search.embedding
    if model_name is not None:
        search_embedding = search_embedding.model_copy(update={"model_name": model_name})

    if search_indexing is config.search.indexing and search_embedding is config.search.embedding:
        return config

    search_config = config.search.model_copy(
        update={
            "indexing": search_indexing,
            "embedding": search_embedding,
        }
    )
    return config.model_copy(update={"search": search_config})


__all__ = ["run_search_index_stage"]