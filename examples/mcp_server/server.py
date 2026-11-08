"""A local stdio host for the fictional second-brain index."""

from __future__ import annotations

import argparse
from pathlib import Path

from fastmcp import FastMCP

from matlock2.embedding import FastEmbedder
from matlock2_mcp import SearchConfig, SearchService
from matlock2_mcp.registration import register_tools


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path,
        default=Path(__file__).resolve().parents[1] / "second-brain" / ".matlock2" / "index.sqlite3",
    )
    parser.add_argument(
        "--enable-semantic", action="store_true",
        help="Attest the example index uses the configured default model and preprocessing",
    )
    args = parser.parse_args()
    options = {}
    embedder = None
    if args.enable_semantic:
        embedder = FastEmbedder("BAAI/bge-small-en-v1.5", 384, 512)
        options = {
            "embedding_space": "example-bge-small-en-v1.5",
            "embedding_model": embedder.model_name,
            "embedding_version": embedder.version,
            "embedding_dimensions": embedder.dimensions,
        }
    service = SearchService(SearchConfig(database_path=args.database, **options), embedder)
    mcp = FastMCP("Matlock2 example")
    register_tools(mcp, service)
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
