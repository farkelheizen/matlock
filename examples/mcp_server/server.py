"""A local stdio host for the fictional second-brain index."""

from __future__ import annotations

import argparse
from pathlib import Path

from fastmcp import FastMCP

from matlock2_mcp.registration import register_tools

if __package__:
    from .example_search import create_search_service
else:
    from example_search import create_search_service


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
    service = create_search_service(args.database, args.enable_semantic)
    mcp = FastMCP("Matlock2 example")
    register_tools(mcp, service)
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
