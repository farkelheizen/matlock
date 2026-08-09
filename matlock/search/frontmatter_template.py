from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")


def _resolve_path(value: Any, path: list[str]) -> Any:
    current = value
    for segment in path:
        if isinstance(current, Mapping):
            current = current.get(segment)
            continue
        return None
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, default=str, sort_keys=True)


def render_frontmatter_template(
    template: str,
    *,
    frontmatter: Mapping[str, Any] | None = None,
    system: Mapping[str, Any] | None = None,
    database: Mapping[str, Any] | None = None,
) -> str:
    """Render a namespaced frontmatter template safely.

    Supported namespaces are ``fm`` for parsed frontmatter, ``sys`` for file
    metadata, and ``db`` for Matlock relational context. Missing values resolve
    to an empty string.
    """

    contexts: dict[str, Mapping[str, Any]] = {
        "fm": frontmatter or {},
        "sys": system or {},
        "db": database or {},
    }

    def _replace(match: re.Match[str]) -> str:
        field = match.group(1)
        parts = field.split(".")
        namespace = parts[0]
        root = contexts.get(namespace)
        if root is None:
            return ""
        resolved = _resolve_path(root, parts[1:])
        return _stringify(resolved)

    return _FIELD_RE.sub(_replace, template)


__all__ = ["render_frontmatter_template"]