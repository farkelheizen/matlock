from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db
from matlock.search.models import MatlockSearchResponse, SearchResponseResult, SearchResponseStats
from matlock.search.query_cli import SearchCliOutcome

runner = CliRunner()


def _write_config(path: Path, base_dir: Path, db_path: Path, out_dir: Path | None = None) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(out_dir or base_dir / "_output"),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _seed_projects(db_path: Path) -> None:
    conn = get_connection(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)",
        ("sp-2", "Alpha Team", "P1"),
    )
    conn.execute(
        "INSERT INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)",
        ("sp-1", "Beta Team", "P2"),
    )
    conn.execute(
        "INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("proj-b", "sp-2", "Roadmap: 100%", "docs/b.md", "P2", "Active", "2026-01-01", None),
    )
    conn.execute(
        "INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("proj-a", "sp-1", "Alpha", "docs/a.md", "P1", "Active", "2026-01-02", "2026-03-02"),
    )
    conn.commit()
    conn.close()


def test_find_projects_returns_all_projects_json(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["project_id"] for row in payload] == ["proj-a", "proj-b"]


def test_find_projects_direct_term_matches_any_project_column(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects", "100%"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["project_id"] for row in payload] == ["proj-b"]


def test_list_super_projects_returns_all_rows(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    result = runner.invoke(app, ["--config", str(cfg_path), "list-super-projects"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["super_project_id"] for row in payload] == ["sp-1", "sp-2"]


def test_list_tasks_returns_active_unlinked_tasks_and_filters(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)

    conn = get_connection(db_path)
    init_db(conn)
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/live.md", "hash-1", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/archived.md", "hash-2", ".md", 1, 1, "2026-01-01", 1, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/unlinked.md", "hash-3", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("proj-a", None, "Alpha", "notes/live.md", "P1", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("proj-b", None, "Beta", "notes/other.md", "P2", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/live.md", "proj-a"))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/live.md", "proj-b"))
    conn.execute("INSERT INTO task (task_id, file_path, parent_task_id, created_date, due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, twin_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("t-1", "notes/live.md", None, "2026-01-01", "2026-03-02", None, None, 1, "Alpha task", 0, '["ops"]', '{"owner":"ops"}', '[]', 0))
    conn.execute("INSERT INTO task (task_id, file_path, parent_task_id, created_date, due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, twin_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("t-2", "notes/live.md", None, "2026-01-01", "2026-03-04", None, None, 0, "Beta task", 0, '[]', '{}', '[]', 0))
    conn.execute("INSERT INTO task (task_id, file_path, parent_task_id, created_date, due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, twin_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("t-3", "notes/archived.md", None, "2026-01-01", "2026-03-03", None, None, 0, "Archived", 0, '[]', '{}', '[]', 0))
    conn.execute("INSERT INTO task (task_id, file_path, parent_task_id, created_date, due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, twin_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("t-4", "notes/unlinked.md", None, "2026-01-01", "2026-03-05", None, None, 0, "Unlinked task", 0, '[]', '{}', '[]', 0))
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["--config", str(cfg_path), "list-tasks", "--checked", "--task-text", "alpha"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["task_id"] for row in payload] == ["t-1"]

    result = runner.invoke(app, ["--config", str(cfg_path), "list-tasks", "--project-id", "proj-b", "--due-date", ">=2026-03-03", "--due-date", "<=2026-03-04"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["task_id"] for row in payload] == ["t-2"]

    result = runner.invoke(app, ["--config", str(cfg_path), "list-tasks", "--due-date", "bad"])
    assert result.exit_code == 1
    assert "invalid date predicate" in result.output.lower()


def test_find_projects_search_files_returns_union_and_ranking(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    conn = get_connection(db_path)
    init_db(conn)
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("docs/alpha.md", "hash-a", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("docs/beta.md", "hash-b", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("docs/alpha.md", "proj-a"))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("docs/beta.md", "proj-b"))
    conn.execute("UPDATE project SET home_file = ? WHERE project_id = ?", ("docs/alpha.md", "proj-a"))
    conn.execute("UPDATE project SET home_file = ? WHERE project_id = ?", ("docs/beta.md", "proj-b"))
    conn.commit()
    conn.close()

    def fake_run_search_request(config, conn, request_data):
        response = MatlockSearchResponse(
            status="success",
            stats=SearchResponseStats(
                total_matches=2,
                returned_matches=2,
                query_time_ms=3.0,
                search_mode_executed=request_data["search_mode"],
            ),
            results=[
                SearchResponseResult(
                    file_path="docs/alpha.md",
                    absolute_path="file:///tmp/docs/alpha.md",
                    project_id="proj-a",
                    score=0.60,
                    score_breakdown={},
                    created="2026-01-01T00:00:00Z",
                    modified="2026-01-01T00:00:00Z",
                    frontmatter={},
                    has_secrets=False,
                    secret_detection_error=None,
                ),
                SearchResponseResult(
                    file_path="docs/beta.md",
                    absolute_path="file:///tmp/docs/beta.md",
                    project_id="proj-b",
                    score=0.95,
                    score_breakdown={},
                    created="2026-01-01T00:00:00Z",
                    modified="2026-01-01T00:00:00Z",
                    frontmatter={},
                    has_secrets=False,
                    secret_detection_error=None,
                ),
            ],
            error=None,
        )
        return SearchCliOutcome(response=response, exit_code=0)

    monkeypatch.setattr("matlock.cli.run_search_request", fake_run_search_request)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects", "alpha", "--search-files"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["project_id"] for row in payload] == ["proj-b", "proj-a"]


def test_find_projects_search_files_requires_text(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects", "--search-files"])

    assert result.exit_code == 1
    assert "text is required" in result.output.lower()


def test_find_projects_search_files_propagates_search_error(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    def fake_run_search_request(config, conn, request_data):
        from matlock.search.query_cli import build_error_response
        return SearchCliOutcome(
            response=build_error_response(
                error_code="embedding_error",
                message="simulated search failure",
                search_mode=request_data["search_mode"],
            ),
            exit_code=3,
        )

    monkeypatch.setattr("matlock.cli.run_search_request", fake_run_search_request)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects", "alpha", "--search-files"])

    assert result.exit_code == 3
    assert "simulated search failure" in result.output.lower()
