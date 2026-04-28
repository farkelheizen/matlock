"""Tests for matlock/cli.py — map-projects subcommand."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db, upsert_file

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(
    path: Path,
    base_dir: Path,
    db_path: Path,
    super_projects: list | None = None,
    projects: list | None = None,
) -> None:
    cfg: dict = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(base_dir / "_output"),
    }
    if super_projects:
        cfg["super_projects"] = super_projects
    if projects:
        cfg["projects"] = projects
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _seed_db_file(db_path: Path, file_path: str) -> None:
    """Insert a file row directly into the DB (no sync needed)."""
    conn = get_connection(db_path)
    init_db(conn)
    upsert_file(conn, {
        "file_path": file_path,
        "sha256": "aaa",
        "file_ext": ".md",
        "created": 0,
        "modified": 0,
        "modified_date": "2026-01-01",
        "deleted": 0,
        "length": 0,
        "word_count": None,
        "meta_data": None,
        "is_generated": 0,
        "needs_parsing": 0,
    })
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestMapProjectsHelp:
    def test_map_projects_help(self):
        result = runner.invoke(app, ["map-projects", "--help"])
        assert result.exit_code == 0
        assert "project" in result.output.lower()

    def test_map_projects_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "map-projects" in result.output


# ---------------------------------------------------------------------------
# map-projects — happy path
# ---------------------------------------------------------------------------


class TestMapProjectsCommand:
    def test_exits_zero(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0

    def test_summary_format(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(
            cfg_path, vault, db_path,
            super_projects=[{"id": "sp1", "title": "SP One"}],
            projects=[{"id": "p1", "title": "P One", "resources": []}],
        )

        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0
        assert "Map-projects complete:" in result.output
        assert "1 super-projects" in result.output
        assert "1 projects" in result.output
        assert "0 file-project links" in result.output

    def test_file_project_rows_in_db(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "plan.md").write_text("# Plan", encoding="utf-8")
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(
            cfg_path, vault, db_path,
            projects=[{
                "id": "p1",
                "title": "Project One",
                "resources": [{"type": "FILE", "path": "plan.md"}],
            }],
        )

        # Sync first so file row exists in DB
        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0
        assert "1 file-project links" in result.output

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM file_project").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["project_id"] == "p1"

    def test_directory_resource_matches_files(self, tmp_path: Path):
        vault = tmp_path / "vault"
        tech = vault / "Tech"
        tech.mkdir(parents=True)
        (tech / "a.md").write_text("A", encoding="utf-8")
        (tech / "b.md").write_text("B", encoding="utf-8")
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(
            cfg_path, vault, db_path,
            projects=[{
                "id": "p1",
                "title": "Tech",
                "resources": [{"type": "DIRECTORY", "path": "Tech"}],
            }],
        )

        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0
        assert "2 file-project links" in result.output

    def test_second_run_is_noop(self, tmp_path: Path):
        """Running map-projects twice should produce identical results."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "plan.md").write_text("# Plan", encoding="utf-8")
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(
            cfg_path, vault, db_path,
            projects=[{
                "id": "p1",
                "title": "P One",
                "resources": [{"type": "FILE", "path": "plan.md"}],
            }],
        )

        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        runner.invoke(app, ["--config", str(cfg_path), "map-projects"])

        result2 = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result2.exit_code == 0
        assert "1 file-project links" in result2.output

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM file_project").fetchone()[0]
        conn.close()
        assert count == 1

    def test_no_projects_in_config(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0
        assert "0 super-projects" in result.output
        assert "0 projects" in result.output
        assert "0 file-project links" in result.output

    def test_creates_db_if_not_exists(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        assert not db_path.exists()
        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code == 0
        assert db_path.exists()


# ---------------------------------------------------------------------------
# map-projects — error cases
# ---------------------------------------------------------------------------


class TestMapProjectsErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "map-projects",
        ])
        assert result.exit_code != 0

    def test_invalid_config_exits_nonzero(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("not: valid: yaml: config", encoding="utf-8")
        result = runner.invoke(app, ["--config", str(cfg_path), "map-projects"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Subprocess smoke test
# ---------------------------------------------------------------------------


class TestMapProjectsSubprocess:
    def test_subprocess_smoke(self):
        """Verify the module can be invoked without import errors."""
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr
