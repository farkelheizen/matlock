"""Example metadata enrichment hook for project notes."""

from matlock2.models import DocumentContext


def project_id_from_path(context: DocumentContext, metadata: dict[str, object]) -> None:
    parts = context.relative_path.split("/")
    if (
        len(parts) == 3
        and parts[0] == "projects"
        and parts[1] not in ("", ".", "..")
        and parts[2].lower().endswith(".md")
        and parts[2].lower() != ".md"
        and metadata.get("project_id") is None
    ):
        metadata["project_id"] = parts[1]
