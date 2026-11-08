from __future__ import annotations

from .models import DocumentContext


def project_id_from_path(context: DocumentContext, metadata: dict[str, object]) -> None:
    """Set project_id from a path component below a `projects/` directory."""
    parts = context.relative_path.split("/")
    try:
        projects_index = parts.index("projects")
    except ValueError:
        return
    if projects_index + 1 < len(parts):
        metadata.setdefault("project_id", parts[projects_index + 1])
