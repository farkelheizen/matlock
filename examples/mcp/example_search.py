"""Shared search configuration for the example MCP hosts."""

from pathlib import Path

from matlock_ingest.embedding import FastEmbedder
from matlock_mcp import SearchConfig, SearchService
from matlock_mcp.policy import SearchPolicy


def create_search_service(
    database: Path, enable_semantic: bool, policy: SearchPolicy | None = None
) -> SearchService:
    if not enable_semantic:
        return SearchService(SearchConfig(database_path=database), policy=policy)

    embedder = FastEmbedder("BAAI/bge-small-en-v1.5", 384, 512)
    config = SearchConfig(
        database_path=database,
        embedding_space="example-bge-small-en-v1.5",
        embedding_model=embedder.model_name,
        embedding_version=embedder.version,
        embedding_dimensions=embedder.dimensions,
    )
    return SearchService(config, embedder, policy)
