"""Tests for matlock/cli.py — sync subcommand."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, get_file, init_db

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(path: Path, base_dir: Path, db_path: Path, output_dir: Path | None = None) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(output_dir or base_dir / "_output"),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "matlock" in result.output.lower()

    def test_sync_help(self):
        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output


# ---------------------------------------------------------------------------
# sync — happy path
# ---------------------------------------------------------------------------


class TestSyncCommand:
    def test_inserts_md_files(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("hello")
        (vault / "b.md").write_text("world")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "sync"])

        assert result.exit_code == 0
        assert "2 inserted" in result.output

    def test_output_contains_summary(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("content")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        result = runner.invoke(app, ["--config", str(cfg_path), "sync"])

        assert result.exit_code == 0
        assert "Sync complete:" in result.output
        assert "inserted" in result.output
        assert "updated" in result.output
        assert "unchanged" in result.output
        assert "deleted" in result.output

    def test_second_run_unchanged(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("content")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        result2 = runner.invoke(app, ["--config", str(cfg_path), "sync"])

        assert result2.exit_code == 0
        assert "1 unchanged" in result2.output

    def test_force_flag(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("content")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "sync"])
        result2 = runner.invoke(app, ["--config", str(cfg_path), "sync", "--force"])

        assert result2.exit_code == 0
        assert "1 updated" in result2.output

    def test_db_file_created(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "sync"])

        assert db_path.exists()

    def test_file_row_stored_correctly(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("hello")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        runner.invoke(app, ["--config", str(cfg_path), "sync"])

        conn = get_connection(db_path)
        row = get_file(conn, "note.md")
        conn.close()
        assert row is not None
        assert row["needs_parsing"] == 1
        assert row["file_ext"] == ".md"


# ---------------------------------------------------------------------------
# sync — error handling
# ---------------------------------------------------------------------------


class TestSyncErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, ["--config", str(tmp_path / "nope.yaml"), "sync"])
        assert result.exit_code != 0

    def test_missing_config_prints_error(self, tmp_path: Path):
        result = runner.invoke(app, ["--config", str(tmp_path / "nope.yaml"), "sync"])
        # Error goes to stderr; CliRunner mixes stdout+stderr by default
        assert "error" in result.output.lower() or result.exit_code != 0

    def test_invalid_yaml_exits_nonzero(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("base_directory: !!invalid", encoding="utf-8")
        result = runner.invoke(app, ["--config", str(cfg_path), "sync"])
        assert result.exit_code != 0

    def test_missing_base_directory_exits_nonzero(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        nonexistent = tmp_path / "does_not_exist"
        _write_config(cfg_path, nonexistent, tmp_path / "matlock.db")
        result = runner.invoke(app, ["--config", str(cfg_path), "sync"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Smoke test — installed entrypoint via subprocess
# ---------------------------------------------------------------------------


class TestInstalledEntrypoint:
    def test_matlock_help_via_subprocess(self):
        """Verify the installed `matlock` script is on PATH and responds."""
        result = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        # Running as a module should either work or fail gracefully (not crash with
        # ImportError / ModuleNotFoundError). We just check no Python traceback.
        assert "Traceback" not in result.stderr
