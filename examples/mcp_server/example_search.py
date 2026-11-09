"""Shared search configuration for the example MCP hosts."""

from pathlib import Path

from matlock2.embedding import FastEmbedder
from matlock2_mcp import SearchConfig, SearchService


def create_search_service(database: Path, enable_semantic: bool) -> SearchService:
    if not enable_semantic:
        return SearchService(SearchConfig(database_path=database))

    embedder = FastEmbedder("BAAI/bge-small-en-v1.5", 384, 512)
    config = SearchConfig(
        database_path=database,
        embedding_space="example-bge-small-en-v1.5",
        embedding_model=embedder.model_name,
        embedding_version=embedder.version,
        embedding_dimensions=embedder.dimensions,
    )
    return SearchService(config, embedder)
