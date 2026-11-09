"""Register selected search tools on a host-owned FastMCP instance."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .search import SearchService

TOOL_NAMES = frozenset({
    "metadata_search", "full_text_search", "semantic_search", "vector_search",
    "hybrid_knowledge_search", "get_distinct_metadata", "get_chunk",
})


def register_tools(
    mcp: FastMCP, service: SearchService, enabled: set[str] | None = None
) -> None:
    """Attach selected tools; the host retains ownership of server lifecycle."""
    default = {"metadata_search", "full_text_search", "get_distinct_metadata", "get_chunk"}
    if service.config.embedding_space:
        default.add("vector_search")
        if service.embedder is not None:
            default.update({"semantic_search", "hybrid_knowledge_search"})
    selected = default if enabled is None else set(enabled)
    if selected - TOOL_NAMES:
        raise ValueError(f"Unknown tools: {', '.join(sorted(selected - TOOL_NAMES))}")
    if selected & {"vector_search", "semantic_search", "hybrid_knowledge_search"} and not service.config.embedding_space:
        raise ValueError("Vector tools require an embedding space configuration")
    if selected & {"semantic_search", "hybrid_knowledge_search"} and service.embedder is None:
        raise ValueError("Semantic tools require a query embedding provider")

    def metadata_search(metadata_filters: dict[str, Any] | None = None, limit: int = 10, offset: int = 0,
                        document_attribute_filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Find documents with AND-ed metadata filters; empty filters browse."""
        return service.metadata_search(metadata_filters, limit, offset, document_attribute_filters)

    def full_text_search(keyword_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
                         document_attribute_filters: dict[str, Any] | None = None,
                         chunk_attribute_filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Find chunks matching a literal phrase; lower FTS score is better."""
        return service.full_text_search(keyword_query, metadata_filters, limit,
                                        document_attribute_filters, chunk_attribute_filters)

    def vector_search(vector: list[float], embedding_space: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
                      document_attribute_filters: dict[str, Any] | None = None,
                      chunk_attribute_filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Find chunks by a compatible vector; lower cosine distance is better."""
        return service.vector_search(vector, embedding_space, metadata_filters, limit,
                                     document_attribute_filters, chunk_attribute_filters)

    def semantic_search(semantic_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
                        document_attribute_filters: dict[str, Any] | None = None,
                        chunk_attribute_filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Embed a query and find chunks by cosine distance."""
        return service.semantic_search(semantic_query, metadata_filters, limit,
                                       document_attribute_filters, chunk_attribute_filters)

    def hybrid_knowledge_search(semantic_query: str, keyword_query: str, metadata_filters: dict[str, Any] | None = None, limit: int = 10,
                                document_attribute_filters: dict[str, Any] | None = None,
                                chunk_attribute_filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Combine literal phrase and semantic results with reciprocal-rank fusion."""
        return service.hybrid_knowledge_search(semantic_query, keyword_query, metadata_filters, limit,
                                               document_attribute_filters, chunk_attribute_filters)

    def get_distinct_metadata(field: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        """List distinct scalar metadata values, including explicit null."""
        return service.get_distinct_metadata(field, limit, offset)

    def get_chunk(chunk_id: str) -> dict[str, Any]:
        """Retrieve an indexed chunk by identifier, or null if absent."""
        return service.get_chunk(chunk_id)

    functions = {
        function.__name__: function
        for function in (metadata_search, full_text_search, vector_search, semantic_search,
                         hybrid_knowledge_search, get_distinct_metadata, get_chunk)
    }
    for name in sorted(selected):
        mcp.tool(functions[name])
