from __future__ import annotations

import argparse
import json
from pathlib import Path

from markdown_stuff.extractor import extract_tasks_from_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract tasks from a Markdown file and print as JSON."
    )
    parser.add_argument(
        "markdown_file",
        nargs="?",
        default="test-data/document-1.md",
        help="Path to the Markdown file to process.",
    )
    parser.add_argument(
        "--config",
        default="config/default_config.json",
        help="Path to the config JSON file.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    markdown_path = Path(args.markdown_file)
    config_path = Path(args.config)

    if not markdown_path.exists():
        print(f"Markdown file not found: {markdown_path}")
        return 1
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        return 1

    with config_path.open(encoding="utf-8") as f:
        config_dict = json.load(f)

    md_string = markdown_path.read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md_string, config_dict)

    print(json.dumps(doc.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
