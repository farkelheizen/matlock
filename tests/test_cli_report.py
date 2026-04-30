"""Tests for matlock/cli.py — report subcommand."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(path: Path, base_dir: Path, db_path: Path, out_dir: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(out_dir),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _invoke(tmp_path: Path, *extra_args):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    out_dir = tmp_path / "_Matlock"
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "matlock.db"
    _write_config(cfg_path, vault, db_path, out_dir)
    args = ["--config", str(cfg_path), "report"] + list(extra_args)
    return runner.invoke(app, args), cfg_path, out_dir, db_path


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestReportHelp:
    def test_report_help(self):
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
        assert "report" in result.output.lower()

    def test_report_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "report" in result.output

    def test_target_option_shown_in_help(self):
        result = runner.invoke(app, ["report", "--help"])
        assert "--target" in result.output

    def test_project_id_option_shown_in_help(self):
        result = runner.invoke(app, ["report", "--help"])
        assert "--project-id" in result.output


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestReportCommand:
    def test_exits_zero(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert result.exit_code == 0

    def test_summary_format(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Report complete:" in result.output
        assert "files written" in result.output

    def test_default_target_is_all(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "(all)" in result.output

    def test_creates_output_directory(self, tmp_path: Path):
        _, _, out_dir, _ = _invoke(tmp_path)[0], *_invoke(tmp_path)[1:]
        result, _, out_dir, _ = _invoke(tmp_path)
        assert out_dir.exists()

    def test_dashboard_file_written(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path)
        assert result.exit_code == 0
        assert (out_dir / "000_Daily_Dashboard.md").exists()

    def test_creates_db_if_not_exists(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)
        assert not db_path.exists()
        runner.invoke(app, ["--config", str(cfg_path), "report"])
        assert db_path.exists()

    def test_target_dashboard(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path, "--target", "dashboard")
        assert result.exit_code == 0
        assert "(dashboard)" in result.output
        assert (out_dir / "000_Daily_Dashboard.md").exists()
        assert not (out_dir / "Projects").exists()

    def test_target_projects_no_projects(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path, "--target", "projects")
        assert result.exit_code == 0
        assert "(projects)" in result.output

    def test_target_history_no_metrics(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path, "--target", "history")
        assert result.exit_code == 0
        assert "(history)" in result.output

    def test_second_run_idempotent(self, tmp_path: Path):
        _invoke(tmp_path)
        result, _, out_dir, _ = _invoke(tmp_path)
        assert result.exit_code == 0
        assert (out_dir / "000_Daily_Dashboard.md").exists()

    def test_project_id_flag(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        # Pre-seed a project
        conn = get_connection(db_path)
        init_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO project (project_id, title) VALUES (?, ?)",
            ("alpha", "Alpha"),
        )
        conn.commit()
        conn.close()

        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "report", "--project-id", "alpha"],
        )
        assert result.exit_code == 0
        assert (out_dir / "Projects" / "alpha.md").exists()

    def test_written_files_registered_as_generated(self, tmp_path: Path):
        result, _, out_dir, db_path = _invoke(tmp_path)
        conn = get_connection(db_path)
        path = str(out_dir / "000_Daily_Dashboard.md")
        row = conn.execute(
            "SELECT is_generated FROM file WHERE file_path = ?", (path,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["is_generated"] == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestReportErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "report",
        ])
        assert result.exit_code != 0

    def test_invalid_target_exits_nonzero(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--target", "bogus")
        assert result.exit_code != 0

    def test_invalid_target_error_message(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--target", "invalid_value")
        assert "invalid" in result.output.lower() or "invalid" in (result.stderr or "").lower()


# ---------------------------------------------------------------------------
# Subprocess smoke test
# ---------------------------------------------------------------------------


class TestReportSubprocess:
    def test_subprocess_smoke(self):
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# --force flag
# ---------------------------------------------------------------------------


class TestReportForce:
    def test_force_option_shown_in_help(self):
        result = runner.invoke(app, ["report", "--help"])
        assert "--force" in result.output

    def test_force_exits_zero(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--force")
        assert result.exit_code == 0

    def test_force_summary_format(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--force")
        assert "Report complete:" in result.output
        assert "files written" in result.output

    def test_force_deletes_orphaned_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        # Run once to create the output directory
        runner.invoke(app, ["--config", str(cfg_path), "report"])

        # Plant an orphaned file
        orphan = out_dir / "stale_orphan.md"
        orphan.write_text("old content")

        result = runner.invoke(app, ["--config", str(cfg_path), "report", "--force"])

        assert result.exit_code == 0
        assert not orphan.exists()


# ---------------------------------------------------------------------------
# --force-report flag on run-all
# ---------------------------------------------------------------------------


class TestRunAllForceReport:
    def _invoke_run_all(self, tmp_path: Path, *extra_args):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)
        args = ["--config", str(cfg_path), "run-all"] + list(extra_args)
        return runner.invoke(app, args), out_dir

    def test_force_report_option_shown_in_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert "--force-report" in result.output

    def test_force_report_exits_zero(self, tmp_path: Path):
        result, _ = self._invoke_run_all(tmp_path, "--force-report", "--skip-rollup")
        assert result.exit_code == 0

    def test_force_report_deletes_orphaned_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        runner.invoke(app, ["--config", str(cfg_path), "run-all", "--skip-rollup"])
        orphan = out_dir / "stale.md"
        orphan.write_text("stale")

        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "run-all", "--skip-rollup", "--force-report"],
        )

        assert result.exit_code == 0
        assert not orphan.exists()
