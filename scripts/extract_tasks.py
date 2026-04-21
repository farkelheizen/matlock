from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from markdown_stuff.extractor import extract_tasks_from_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract tasks from a Markdown file and print as JSON."
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        default="test-data/document-1.md",
        help="Path to the Markdown file to process, relative to --base-path.",
    )
    parser.add_argument(
        "--base-path",
        default=".",
        help="Base directory used to resolve file_path.",
    )
    parser.add_argument(
        "--config",
        default="config/default_config.json",
        help="Path to the config JSON file.",
    )
    return parser


def build_file_metadata(markdown_path: Path, markdown_text: str) -> dict[str, int | str]:
    stat_result = markdown_path.stat()
    created_timestamp = getattr(stat_result, "st_birthtime", stat_result.st_ctime)
    return {
        "created": int(created_timestamp * 1000),
        "modified": int(stat_result.st_mtime * 1000),
        "length": stat_result.st_size,
        "word_count": len(re.findall(r"\S+", markdown_text)),
        "sha256": hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
    }


def main() -> int:
    args = build_parser().parse_args()

    base_path = Path(args.base_path)
    markdown_path = base_path / args.file_path
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
    doc = extract_tasks_from_markdown(md_string, config_dict, file_path=args.file_path)
    doc = doc.model_copy(update=build_file_metadata(markdown_path.resolve(), md_string))

    print(json.dumps(doc.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
