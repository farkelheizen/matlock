"""Tests for matlock/stages/map_projects.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from matlock.config import MatlockConfig, ProjectConfig, ResourceConfig, SuperProjectConfig
from matlock.db import get_connection, init_db, upsert_file
from matlock.stages.map_projects import (
    MapProjectsResult,
    _is_under,
    _match_files,
    run_map_projects,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _make_config(
    super_projects: list[SuperProjectConfig] | None = None,
    projects: list[ProjectConfig] | None = None,
) -> MatlockConfig:
    return MatlockConfig(
        base_directory=Path("/vault"),
        db_path=Path("/vault/matlock.db"),
        output_directory=Path("/vault/_output"),
        super_projects=super_projects or [],
        projects=projects or [],
    )


def _seed_file(
    conn,
    file_path: str,
    *,
    deleted: int = 0,
    is_generated: int = 0,
) -> None:
    upsert_file(conn, {
        "file_path": file_path,
        "sha256": "aaa",
        "file_ext": ".md",
        "created": 0,
        "modified": 0,
        "modified_date": "2026-01-01",
        "deleted": deleted,
        "length": 0,
        "word_count": None,
        "meta_data": None,
        "is_generated": is_generated,
        "needs_parsing": 0,
    })


def _sp(sid: str = "sp1", title: str = "SP1") -> SuperProjectConfig:
    return SuperProjectConfig(id=sid, title=title)


def _proj(
    pid: str,
    resources: list[ResourceConfig] | None = None,
    super_project_id: str | None = None,
) -> ProjectConfig:
    return ProjectConfig(
        id=pid,
        title=pid.title(),
        super_project_id=super_project_id,
        resources=resources or [],
    )


def _file_res(path: str) -> ResourceConfig:
    return ResourceConfig(type="FILE", path=path)


def _dir_res(path: str) -> ResourceConfig:
    return ResourceConfig(type="DIRECTORY", path=path)


# ---------------------------------------------------------------------------
# _is_under
# ---------------------------------------------------------------------------


class TestIsUnder:
    def test_file_directly_inside_dir(self):
        assert _is_under("Tech/Backend/notes.md", "Tech/Backend") is True

    def test_file_directly_inside_dir_with_trailing_slash(self):
        assert _is_under("Tech/Backend/notes.md", "Tech/Backend/") is True

    def test_file_in_nested_subdir(self):
        assert _is_under("Tech/Backend/sub/deep.md", "Tech/Backend") is True

    def test_does_not_match_partial_dirname(self):
        # "Tech/BackendExtra/file.md" must NOT match "Tech/Backend"
        assert _is_under("Tech/BackendExtra/file.md", "Tech/Backend") is False

    def test_does_not_match_sibling(self):
        assert _is_under("Tech/Frontend/notes.md", "Tech/Backend") is False

    def test_does_not_match_empty_dir(self):
        assert _is_under("notes.md", "") is False

    def test_root_level_dir_matches(self):
        assert _is_under("Projects/plan.md", "Projects") is True


# ---------------------------------------------------------------------------
# _match_files
# ---------------------------------------------------------------------------


class TestMatchFiles:
    def test_file_resource_exact_match(self):
        paths = ["Projects/plan.md", "Notes/diary.md"]
        result = _match_files(paths, "proj1", [_file_res("Projects/plan.md")])
        assert result == [{"file_path": "Projects/plan.md", "project_id": "proj1"}]

    def test_file_resource_no_match(self):
        paths = ["Notes/diary.md"]
        result = _match_files(paths, "proj1", [_file_res("Projects/plan.md")])
        assert result == []

    def test_directory_resource_matches_files_under(self):
        paths = ["Tech/Backend/a.md", "Tech/Backend/sub/b.md", "Tech/Frontend/c.md"]
        result = _match_files(paths, "proj1", [_dir_res("Tech/Backend")])
        file_paths = [r["file_path"] for r in result]
        assert "Tech/Backend/a.md" in file_paths
        assert "Tech/Backend/sub/b.md" in file_paths
        assert "Tech/Frontend/c.md" not in file_paths

    def test_directory_resource_with_trailing_slash(self):
        paths = ["Tech/Backend/a.md"]
        result = _match_files(paths, "proj1", [_dir_res("Tech/Backend/")])
        assert len(result) == 1

    def test_file_matched_only_once_despite_multiple_resources(self):
        # A file matching two resources for the same project should appear once
        paths = ["Projects/plan.md"]
        resources = [_dir_res("Projects"), _file_res("Projects/plan.md")]
        result = _match_files(paths, "proj1", resources)
        assert len(result) == 1

    def test_empty_resources_returns_empty(self):
        paths = ["a.md", "b.md"]
        result = _match_files(paths, "proj1", [])
        assert result == []

    def test_empty_paths_returns_empty(self):
        result = _match_files([], "proj1", [_dir_res("Tech/")])
        assert result == []

    def test_project_id_propagated(self):
        paths = ["a.md"]
        result = _match_files(paths, "my_project", [_file_res("a.md")])
        assert result[0]["project_id"] == "my_project"


# ---------------------------------------------------------------------------
# run_map_projects — super_project / project tables
# ---------------------------------------------------------------------------


class TestRunMapProjectsTables:
    def test_super_projects_written(self):
        conn = _conn()
        config = _make_config(
            super_projects=[_sp("sp1", "SP One"), _sp("sp2", "SP Two")],
        )
        result = run_map_projects(config, conn)
        assert result.super_projects_written == 2
        rows = conn.execute("SELECT * FROM super_project").fetchall()
        assert len(rows) == 2

    def test_projects_written(self):
        conn = _conn()
        config = _make_config(
            super_projects=[_sp("sp1")],
            projects=[_proj("p1", super_project_id="sp1"), _proj("p2")],
        )
        result = run_map_projects(config, conn)
        assert result.projects_written == 2
        rows = conn.execute("SELECT * FROM project").fetchall()
        assert len(rows) == 2

    def test_super_project_columns(self):
        conn = _conn()
        config = _make_config(
            super_projects=[SuperProjectConfig(id="sp1", title="My SP", priority="high")],
        )
        run_map_projects(config, conn)
        row = conn.execute("SELECT * FROM super_project WHERE super_project_id = 'sp1'").fetchone()
        assert row["title"] == "My SP"
        assert row["priority"] == "high"

    def test_project_columns(self):
        conn = _conn()
        config = _make_config(
            projects=[ProjectConfig(
                id="p1",
                title="Backend",
                super_project_id=None,
                home_file="Projects/backend.md",
                priority="high",
                start_date="2026-01-01",
                due_date="2026-06-01",
            )],
        )
        run_map_projects(config, conn)
        row = conn.execute("SELECT * FROM project WHERE project_id = 'p1'").fetchone()
        assert row["title"] == "Backend"
        assert row["home_file"] == "Projects/backend.md"
        assert row["priority"] == "high"
        assert row["start_date"] == "2026-01-01"
        assert row["due_date"] == "2026-06-01"
        assert row["super_project_id"] is None

    def test_empty_config_no_error(self):
        conn = _conn()
        config = _make_config()
        result = run_map_projects(config, conn)
        assert result.super_projects_written == 0
        assert result.projects_written == 0
        assert result.file_project_rows == 0

    def test_returns_map_projects_result_type(self):
        conn = _conn()
        config = _make_config()
        result = run_map_projects(config, conn)
        assert isinstance(result, MapProjectsResult)


# ---------------------------------------------------------------------------
# run_map_projects — file_project matching
# ---------------------------------------------------------------------------


class TestRunMapProjectsFileMatching:
    def test_file_resource_produces_link(self):
        conn = _conn()
        _seed_file(conn, "Projects/plan.md")
        config = _make_config(
            projects=[_proj("p1", resources=[_file_res("Projects/plan.md")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 1
        rows = conn.execute("SELECT * FROM file_project").fetchall()
        assert rows[0]["file_path"] == "Projects/plan.md"
        assert rows[0]["project_id"] == "p1"

    def test_directory_resource_produces_links(self):
        conn = _conn()
        _seed_file(conn, "Tech/Backend/a.md")
        _seed_file(conn, "Tech/Backend/sub/b.md")
        _seed_file(conn, "Tech/Frontend/c.md")
        config = _make_config(
            projects=[_proj("p1", resources=[_dir_res("Tech/Backend")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 2
        paths = {r["file_path"] for r in conn.execute("SELECT file_path FROM file_project").fetchall()}
        assert paths == {"Tech/Backend/a.md", "Tech/Backend/sub/b.md"}

    def test_deleted_files_excluded(self):
        conn = _conn()
        _seed_file(conn, "Projects/plan.md", deleted=1)
        config = _make_config(
            projects=[_proj("p1", resources=[_file_res("Projects/plan.md")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 0

    def test_generated_files_excluded(self):
        conn = _conn()
        _seed_file(conn, "_Matlock/dashboard.md", is_generated=1)
        config = _make_config(
            projects=[_proj("p1", resources=[_dir_res("_Matlock")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 0

    def test_file_belongs_to_multiple_projects(self):
        conn = _conn()
        _seed_file(conn, "Shared/notes.md")
        config = _make_config(
            projects=[
                _proj("p1", resources=[_file_res("Shared/notes.md")]),
                _proj("p2", resources=[_dir_res("Shared")]),
            ],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 2
        rows = conn.execute("SELECT project_id FROM file_project ORDER BY project_id").fetchall()
        assert [r["project_id"] for r in rows] == ["p1", "p2"]

    def test_project_with_no_resources_produces_no_links(self):
        conn = _conn()
        _seed_file(conn, "Notes/diary.md")
        config = _make_config(
            projects=[_proj("p1", resources=[])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 0
        assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1

    def test_file_resource_not_in_db_produces_no_link(self):
        conn = _conn()
        # file row does not exist in DB
        config = _make_config(
            projects=[_proj("p1", resources=[_file_res("Projects/missing.md")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 0

    def test_mixed_file_and_directory_resources(self):
        conn = _conn()
        _seed_file(conn, "Projects/plan.md")
        _seed_file(conn, "Tech/Backend/api.md")
        config = _make_config(
            projects=[_proj("p1", resources=[
                _file_res("Projects/plan.md"),
                _dir_res("Tech/Backend"),
            ])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 2


# ---------------------------------------------------------------------------
# run_map_projects — idempotency (full rebuild)
# ---------------------------------------------------------------------------


class TestRunMapProjectsIdempotency:
    def test_second_run_replaces_first(self):
        conn = _conn()
        _seed_file(conn, "Tech/a.md")
        _seed_file(conn, "Tech/b.md")

        config_v1 = _make_config(
            super_projects=[_sp("sp1")],
            projects=[_proj("p1", resources=[_dir_res("Tech")])],
        )
        run_map_projects(config_v1, conn)
        assert conn.execute("SELECT COUNT(*) FROM file_project").fetchone()[0] == 2

        # Second run with a different config — old rows replaced
        config_v2 = _make_config(
            projects=[_proj("p2", resources=[_file_res("Tech/a.md")])],
        )
        result = run_map_projects(config_v2, conn)
        assert result.file_project_rows == 1
        rows = conn.execute("SELECT * FROM file_project").fetchall()
        assert len(rows) == 1
        assert rows[0]["project_id"] == "p2"

    def test_identical_second_run_same_result(self):
        conn = _conn()
        _seed_file(conn, "Projects/plan.md")
        config = _make_config(
            super_projects=[_sp("sp1")],
            projects=[_proj("p1", resources=[_file_res("Projects/plan.md")])],
        )
        r1 = run_map_projects(config, conn)
        r2 = run_map_projects(config, conn)
        assert r1.super_projects_written == r2.super_projects_written
        assert r1.projects_written == r2.projects_written
        assert r1.file_project_rows == r2.file_project_rows
        assert conn.execute("SELECT COUNT(*) FROM file_project").fetchone()[0] == 1

    def test_removes_stale_file_project_rows(self):
        """Verify truncate-then-insert removes rows from a prior run."""
        conn = _conn()
        _seed_file(conn, "Old/stale.md")
        _seed_file(conn, "New/fresh.md")

        config_v1 = _make_config(
            projects=[_proj("p1", resources=[_file_res("Old/stale.md")])],
        )
        run_map_projects(config_v1, conn)
        assert conn.execute("SELECT COUNT(*) FROM file_project").fetchone()[0] == 1

        config_v2 = _make_config(
            projects=[_proj("p1", resources=[_file_res("New/fresh.md")])],
        )
        run_map_projects(config_v2, conn)
        paths = {r["file_path"] for r in conn.execute("SELECT file_path FROM file_project").fetchall()}
        assert paths == {"New/fresh.md"}


# ---------------------------------------------------------------------------
# run_map_projects — directory boundary edge cases
# ---------------------------------------------------------------------------


class TestDirectoryBoundary:
    def test_partial_dirname_not_matched(self):
        """'Tech/BackendExtra/file.md' must not match resource 'Tech/Backend'."""
        conn = _conn()
        _seed_file(conn, "Tech/BackendExtra/file.md")
        config = _make_config(
            projects=[_proj("p1", resources=[_dir_res("Tech/Backend")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 0

    def test_trailing_slash_in_resource_path_still_matches(self):
        conn = _conn()
        _seed_file(conn, "Tech/Backend/notes.md")
        config = _make_config(
            projects=[_proj("p1", resources=[_dir_res("Tech/Backend/")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 1

    def test_deeply_nested_file_matched_by_ancestor_dir(self):
        conn = _conn()
        _seed_file(conn, "Tech/Backend/v2/api/routes.md")
        config = _make_config(
            projects=[_proj("p1", resources=[_dir_res("Tech")])],
        )
        result = run_map_projects(config, conn)
        assert result.file_project_rows == 1
