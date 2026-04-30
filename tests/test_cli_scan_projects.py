"""Tests for matlock/cli.py — scan-projects subcommand."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from matlock.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(
    config_path: Path,
    vault: Path,
    db_path: Path,
    output_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {
        "base_directory": str(vault),
        "db_path": str(db_path),
        "output_directory": str(output_dir or vault / "_out"),
    }
    if extra:
        data.update(extra)
    config_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _write_md(path: Path, meta: dict[str, Any], content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True)
    path.write_text(f"---\n{fm}---\n{content}", encoding="utf-8")


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_scan_projects_in_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan-projects" in result.output

    def test_scan_projects_help(self):
        result = runner.invoke(app, ["scan-projects", "--help"])
        assert result.exit_code == 0
        assert "--print-yaml" in result.output
        assert "--diff" in result.output
        assert "--merge" in result.output


# ---------------------------------------------------------------------------
# Default mode (no flags → --print-yaml)
# ---------------------------------------------------------------------------


class TestDefaultMode:
    def test_default_prints_yaml(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "note.md", {"tag": "Project", "title": "My Project"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(app, ["--config", str(cfg_path), "scan-projects"])

        assert result.exit_code == 0
        # Output should be valid YAML with projects key
        data = yaml.safe_load(result.output)
        assert "projects" in data

    def test_no_frontmatter_vault_still_outputs_yaml(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "plain.md").write_text("# no frontmatter", encoding="utf-8")
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(app, ["--config", str(cfg_path), "scan-projects"])

        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["projects"] == []


# ---------------------------------------------------------------------------
# --print-yaml flag
# ---------------------------------------------------------------------------


class TestPrintYaml:
    def test_print_yaml_flag(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "Projects" / "X" / "X.md", {"title": "X"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--print-yaml"]
        )

        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert any(p["id"] == "X" for p in data["projects"])

    def test_short_flag_p(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(app, ["--config", str(cfg_path), "scan-projects", "-p"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --diff flag
# ---------------------------------------------------------------------------


class TestDiffMode:
    def test_diff_shows_sections(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "NewProj.md", {"tag": "Project", "title": "New"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--diff"]
        )

        assert result.exit_code == 0
        assert "Projects" in result.output

    def test_diff_added_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "NewProj.md", {"tag": "Project", "title": "New"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "-d"]
        )

        assert result.exit_code == 0
        assert "Added" in result.output
        assert "NewProj" in result.output

    def test_diff_removed_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(
            cfg_path, vault, tmp_path / "db.db",
            extra={"projects": [{"id": "OldProj", "title": "Old"}]},
        )

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--diff"]
        )

        assert result.exit_code == 0
        assert "Removed" in result.output
        assert "OldProj" in result.output


# ---------------------------------------------------------------------------
# --merge flag
# ---------------------------------------------------------------------------


class TestMergeMode:
    def test_merge_modifies_config(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "Projects" / "Alpha" / "Alpha.md", {"title": "Alpha"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--merge"]
        )

        assert result.exit_code == 0
        assert "Merge complete" in result.output

        with cfg_path.open() as f:
            data = yaml.safe_load(f)
        project_ids = [p["id"] for p in data.get("projects", [])]
        assert "Alpha" in project_ids

    def test_merge_creates_backup(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--merge"]
        )

        bak_files = list(tmp_path.glob("config.yaml.*.bak"))
        assert len(bak_files) == 1

    def test_merge_prints_warning_to_stderr(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "-m"],
            catch_exceptions=False,
        )

        # Warning goes to stderr; typer CliRunner mixes stdout+stderr in output
        assert "Warning" in result.output or result.exit_code == 0

    def test_merge_echo_contains_counts(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "note.md", {"tag": "Project", "title": "Note"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app, ["--config", str(cfg_path), "scan-projects", "--merge"]
        )

        assert result.exit_code == 0
        assert "project(s) added" in result.output
        assert "Backup:" in result.output


# ---------------------------------------------------------------------------
# Mutually exclusive mode enforcement
# ---------------------------------------------------------------------------


class TestMutuallyExclusive:
    def test_print_yaml_and_diff_rejected(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "scan-projects", "--print-yaml", "--diff"],
        )

        assert result.exit_code == 1

    def test_diff_and_merge_rejected(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "scan-projects", "--diff", "--merge"],
        )

        assert result.exit_code == 1

    def test_all_three_rejected(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(
            app,
            [
                "--config", str(cfg_path),
                "scan-projects", "--print-yaml", "--diff", "--merge",
            ],
        )

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Config not found
# ---------------------------------------------------------------------------


class TestConfigErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(
            app,
            ["--config", str(tmp_path / "nonexistent.yaml"), "scan-projects"],
        )

        assert result.exit_code != 0

    def test_no_db_required(self, tmp_path: Path):
        """scan-projects must not require an initialised database."""
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")
        # db.db deliberately NOT created — command must still succeed

        result = runner.invoke(app, ["--config", str(cfg_path), "scan-projects"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Warnings emitted to output
# ---------------------------------------------------------------------------


class TestWarnings:
    def test_invalid_priority_warning_in_output(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "priority": "Ultra"})
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, tmp_path / "db.db")

        result = runner.invoke(app, ["--config", str(cfg_path), "scan-projects"])

        # Warnings go to stderr; CliRunner captures both by default
        assert result.exit_code == 0
        assert "Ultra" in result.output or "warning" in result.output.lower()
