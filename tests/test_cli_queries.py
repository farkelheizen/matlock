from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db

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


def test_find_projects_rejects_search_files_until_ptq_s3(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path)
    _seed_projects(db_path)

    result = runner.invoke(app, ["--config", str(cfg_path), "find-projects", "alpha", "--search-files"])

    assert result.exit_code == 1
    assert "not yet supported" in result.output.lower()


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
