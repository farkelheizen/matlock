from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import yaml

from .models import ParsedDocument


class MarkdownParseError(ValueError):
    """Raised when Markdown frontmatter cannot be parsed."""


_FRONTMATTER_START = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n")


def _json_value(value: Any) -> Any:
    # YAML dates need conversion before metadata can be serialized as JSON.
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def parse_markdown(relative_path: str, text: str) -> ParsedDocument:
    match = _FRONTMATTER_START.match(text)
    if match is None:
        return ParsedDocument(relative_path, text, {})

    closing = re.search(r"(?m)^(?:---|\.\.\.)[ \t]*\r?$", text[match.end() :])
    if closing is None:
        raise MarkdownParseError("Unterminated YAML frontmatter")

    frontmatter_end = match.end() + closing.start()
    yaml_text = text[match.end() : frontmatter_end]
    body_start = match.end() + closing.end()
    body = text[body_start:].lstrip("\r\n")
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise MarkdownParseError("Invalid YAML frontmatter") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise MarkdownParseError("YAML frontmatter must be a mapping")
    metadata = _json_value(parsed)
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MarkdownParseError("Frontmatter values must be JSON-compatible") from exc
    return ParsedDocument(relative_path, body, metadata)
