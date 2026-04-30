"""Stage III — Map Projects.

Rebuilds the ``super_project``, ``project``, and ``file_project`` tables
from the project hierarchy defined in ``config.yaml``.

This is always a full rebuild: all three tables are truncated and
re-inserted on every run, making the stage idempotent.

Matching rules
--------------
- ``type: FILE``      — ``file.file_path`` equals the resource ``path`` exactly.
- ``type: DIRECTORY`` — ``file.file_path`` starts with the resource ``path`` after
  normalising the path to always end with ``/`` (D1).

Generated files (``is_generated = 1``) and deleted files (``deleted = 1``)
are never included in ``file_project`` associations (D2).
"""

from __future__ import annotations

import dataclasses
import sqlite3

from matlock.config import MatlockConfig, ResourceConfig
from matlock.db import replace_file_projects, replace_projects, replace_super_projects


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class MapProjectsResult:
    """Counts of rows written by :func:`run_map_projects`."""

    super_projects_written: int = 0
    projects_written: int = 0
    file_project_rows: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _all_eligible_paths(conn: sqlite3.Connection) -> list[str]:
    """Return all ``file_path`` values that are non-deleted and non-generated (D2)."""
    rows = conn.execute(
        "SELECT file_path FROM file WHERE deleted = 0 AND is_generated = 0"
    ).fetchall()
    return [row["file_path"] for row in rows]


def _is_under(file_path: str, dir_path: str) -> bool:
    """Return True if *file_path* is directly inside *dir_path*.

    Normalises *dir_path* so that both ``"Tech/Backend"`` and
    ``"Tech/Backend/"`` match ``"Tech/Backend/notes.md"`` but not
    ``"Tech/BackendExtra/notes.md"`` (D1).
    """
    prefix = dir_path.rstrip("/") + "/"
    return file_path.startswith(prefix)


def _match_files(
    all_paths: list[str],
    project_id: str,
    resources: list,
) -> list[dict]:
    """Return ``file_project`` row dicts for *project_id* using Python-side filtering (D3)."""
    matched: list[dict] = []
    for file_path in all_paths:
        for resource in resources:
            if resource.type == "FILE" and file_path == resource.path:
                matched.append({"file_path": file_path, "project_id": project_id})
                break
            if resource.type == "DIRECTORY" and _is_under(file_path, resource.path):
                matched.append({"file_path": file_path, "project_id": project_id})
                break
    return matched


# ---------------------------------------------------------------------------
# Public stage entry point
# ---------------------------------------------------------------------------


def run_map_projects(
    config: MatlockConfig,
    conn: sqlite3.Connection,
) -> MapProjectsResult:
    """Rebuild project tables from *config* and link files via resource rules.

    Uses a two-phase approach to satisfy FK constraints across multiple runs:

    Phase A — clear in reverse-FK order:
        file_project → project → super_project
    Phase B — insert in forward-FK order:
        super_project → project → file_project

    This avoids FK violations when existing rows are present from a prior run.
    Each ``replace_*`` helper in Phase B performs a no-op DELETE on already-empty
    tables, then inserts the new data.
    """
    result = MapProjectsResult()

    # Build row lists from config
    sp_rows = [
        {
            "super_project_id": sp.id,
            "title": sp.title,
            "priority": sp.priority,
        }
        for sp in config.super_projects
    ]
    proj_rows = [
        {
            "project_id": p.id,
            "super_project_id": p.super_project_id,
            "title": p.title,
            "home_file": p.home_file,
            "priority": p.priority,
            "status": p.status,
            "start_date": p.start_date,
            "due_date": p.due_date,
        }
        for p in config.projects
    ]

    # Phase A: clear in reverse-FK order so DELETEs in Phase B are no-ops
    replace_file_projects(conn, [])   # clear file_project first (no FK parent)
    replace_projects(conn, [])        # clear project (file_project now empty)
    # super_project can now be cleared safely (project is empty)

    # Phase B: insert in forward-FK order
    replace_super_projects(conn, sp_rows)   # DELETE super_project (empty) + INSERT
    replace_projects(conn, proj_rows)        # DELETE project (empty) + INSERT
    result.super_projects_written = len(sp_rows)
    result.projects_written = len(proj_rows)

    # Build file_project links — fetch eligible paths once (D3)
    all_paths = _all_eligible_paths(conn)
    fp_rows: list[dict] = []
    for project in config.projects:
        resources = list(project.resources)
        if not resources and project.home_file:
            # When no explicit resources are defined, treat home_file as an
            # implicit FILE resource so the project always links its own file.
            resources = [ResourceConfig(type="FILE", path=project.home_file)]
        fp_rows.extend(_match_files(all_paths, project.id, resources))
    replace_file_projects(conn, fp_rows)    # DELETE file_project (empty) + INSERT
    result.file_project_rows = len(fp_rows)

    conn.commit()
    return result
