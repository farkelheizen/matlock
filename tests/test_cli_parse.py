"""Tests for matlock/cli.py — parse subcommand."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, get_file, get_tasks_for_file, init_db

runner = CliRunner()

_MD_WITH_TASKS = """\
---
status: active
---
# Work

- [ ] Buy milk
- [x] Send email
"""

_MD_NO_TASKS = "# Notes\n\nJust prose.\n"


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


def _sync_then_parse(cfg_path: Path) -> tuple:
    sync_result = runner.invoke(app, ["--config", str(cfg_path), "sync"])
    parse_result = runner.invoke(app, ["--config", str(cfg_path), "parse"])
    return sync_result, parse_result


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestParseHelp:
    def test_parse_help(self):
        result = runner.invoke(app, ["parse", "--help"])
        assert result.exit_code == 0
        assert "parse" in result.output.lower()


# ---------------------------------------------------------------------------
# parse — happy path
# ---------------------------------------------------------------------------


class TestParseCommand:
    def test_parse_after_sync_exits_zero(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _, result = _sync_then_parse(cfg_path)

        assert result.exit_code == 0

    def test_parse_output_contains_summary(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _, result = _sync_then_parse(cfg_path)

        assert "Parse complete:" in result.output
        assert "parsed" in result.output
        assert "skipped" in result.output
        assert "tasks inserted" in result.output
        assert "tasks deleted" in result.output

    def test_tasks_in_db_after_parse(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _sync_then_parse(cfg_path)

        conn = get_connection(db_path)
        tasks = get_tasks_for_file(conn, "tasks.md")
        conn.close()
        assert len(tasks) == 2

    def test_needs_parsing_cleared_after_parse(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _sync_then_parse(cfg_path)

        conn = get_connection(db_path)
        row = get_file(conn, "tasks.md")
        conn.close()
        assert row["needs_parsing"] == 0

    def test_word_count_in_db_after_parse(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _sync_then_parse(cfg_path)

        conn = get_connection(db_path)
        row = get_file(conn, "tasks.md")
        conn.close()
        assert row["word_count"] is not None
        assert row["word_count"] > 0

    def test_meta_data_in_db_after_parse(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _sync_then_parse(cfg_path)

        conn = get_connection(db_path)
        row = get_file(conn, "tasks.md")
        conn.close()
        meta = json.loads(row["meta_data"])
        assert meta.get("status") == "active"

    def test_second_parse_is_noop(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _sync_then_parse(cfg_path)
        result2 = runner.invoke(app, ["--config", str(cfg_path), "parse"])

        assert result2.exit_code == 0
        assert "0 parsed" in result2.output

    def test_no_tasks_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "prose.md").write_text(_MD_NO_TASKS, encoding="utf-8")
        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path)

        _, result = _sync_then_parse(cfg_path)

        assert result.exit_code == 0
        assert "1 parsed" in result.output
        assert "0 tasks inserted" in result.output


# ---------------------------------------------------------------------------
# parse — error handling
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, ["--config", str(tmp_path / "nope.yaml"), "parse"])
        assert result.exit_code != 0

    def test_missing_base_directory_exits_nonzero(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, tmp_path / "nonexistent", tmp_path / "matlock.db")
        result = runner.invoke(app, ["--config", str(cfg_path), "parse"])
        assert result.exit_code != 0
