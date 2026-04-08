from __future__ import annotations

import argparse
import json
from pathlib import Path

from markdown_stuff import parse_ast


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the full marko AST tree for a Markdown file."
    )
    parser.add_argument(
        "markdown_file",
        nargs="?",
        default="test-data/document-1.md",
        help="Path to the Markdown file to parse.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    markdown_path = Path(args.markdown_file)

    if not markdown_path.exists():
        print(f"File not found: {markdown_path}")
        return 1

    markdown_text = markdown_path.read_text(encoding="utf-8")
    ast = parse_ast(markdown_text)

    print(json.dumps(ast, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())