"""Tests for matlock/cli.py — rollup subcommand."""
from __future__ import annotations

import datetime
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


def _write_config(path: Path, base_dir: Path, db_path: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(base_dir / "_output"),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _yesterday() -> str:
    return (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestRollupHelp:
    def test_rollup_help(self):
        result = runner.invoke(app, ["rollup", "--help"])
        assert result.exit_code == 0
        assert "rollup" in result.output.lower()

    def test_rollup_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "rollup" in result.output

    def test_date_option_shown_in_help(self):
        result = runner.invoke(app, ["rollup", "--help"])
        assert "--date" in result.output


# ---------------------------------------------------------------------------
# rollup — happy path
# ---------------------------------------------------------------------------


class TestRollupCommand:
    def test_exits_zero(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "rollup"])
        assert result.exit_code == 0

    def test_summary_format(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "rollup"])
        assert result.exit_code == 0
        assert "Rollup complete:" in result.output
        assert "rows written" in result.output

    def test_defaults_to_yesterday(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "rollup"])
        assert result.exit_code == 0
        assert _yesterday() in result.output

    def test_date_flag_overrides_default(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(
            app, ["--config", str(cfg_path), "rollup", "--date", "2026-01-15"]
        )
        assert result.exit_code == 0
        assert "2026-01-15" in result.output

    def test_creates_db_if_not_exists(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        assert not db_path.exists()
        result = runner.invoke(app, ["--config", str(cfg_path), "rollup"])
        assert result.exit_code == 0
        assert db_path.exists()

    def test_writes_null_row_to_db(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "rollup", "--date", "2026-04-26"])

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL AND metric_date = '2026-04-26'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_second_run_same_date_is_noop(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "rollup", "--date", "2026-04-26"])
        result = runner.invoke(
            app, ["--config", str(cfg_path), "rollup", "--date", "2026-04-26"]
        )
        assert result.exit_code == 0

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM daily_metric").fetchone()[0]
        conn.close()
        assert count == 1  # only the NULL row, no duplicates

    def test_sync_then_rollup_integration(self, tmp_path: Path):
        """Full pipeline: sync creates file rows, rollup uses them."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes.md").write_text("# Notes\n\n- [ ] Do the thing", encoding="utf-8")
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        result = runner.invoke(
            app, ["--config", str(cfg_path), "rollup", "--date", "2026-04-26"]
        )
        assert result.exit_code == 0
        assert "1 rows written" in result.output


# ---------------------------------------------------------------------------
# rollup — error cases
# ---------------------------------------------------------------------------


class TestRollupErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "rollup",
        ])
        assert result.exit_code != 0

    def test_invalid_date_exits_nonzero(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(
            app, ["--config", str(cfg_path), "rollup", "--date", "not-a-date"]
        )
        assert result.exit_code != 0

    def test_invalid_date_error_message(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(
            app, ["--config", str(cfg_path), "rollup", "--date", "2026/04/26"]
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Subprocess smoke test
# ---------------------------------------------------------------------------


class TestRollupSubprocess:
    def test_subprocess_smoke(self):
        """Verify the module can be invoked without import errors."""
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr
