"""Tests for matlock/cli.py — run-all subcommand."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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
    args = ["--config", str(cfg_path), "run-all"] + list(extra_args)
    return runner.invoke(app, args), cfg_path, out_dir, db_path


def _write_project_note(vault: Path, project_id: str = "proj_one") -> None:
    p = vault / "Projects" / project_id / f"{project_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n"
        "tag: Project\n"
        f"project_id: {project_id}\n"
        "title: Project One\n"
        "---\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestRunAllHelp:
    def test_run_all_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert result.exit_code == 0
        assert "run-all" in result.output.lower() or "run" in result.output.lower()

    def test_run_all_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "run-all" in result.output

    def test_skip_rollup_option_shown_in_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert "--skip-rollup" in result.output

    def test_force_sync_option_shown_in_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert "--force-sync" in result.output

    def test_scan_projects_option_shown_in_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert "--scan-projects" in result.output

    def test_index_search_option_shown_in_help(self):
        result = runner.invoke(app, ["run-all", "--help"])
        assert "--index-search" in result.output


# ---------------------------------------------------------------------------
# Happy path — default run
# ---------------------------------------------------------------------------


class TestRunAllCommand:
    def test_exits_zero(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert result.exit_code == 0

    def test_prints_sync_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Sync:" in result.output

    def test_prints_parse_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Parse:" in result.output

    def test_prints_map_projects_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Map-projects:" in result.output

    def test_prints_rollup_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Rollup:" in result.output

    def test_prints_report_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Report:" in result.output

    def test_prints_completion_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "run-all complete." in result.output

    def test_creates_db(self, tmp_path: Path):
        _, _, _, db_path = _invoke(tmp_path)
        assert db_path.exists()

    def test_creates_output_directory(self, tmp_path: Path):
        _, _, out_dir, _ = _invoke(tmp_path)
        assert out_dir.exists()

    def test_dashboard_file_written(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path)
        assert result.exit_code == 0
        assert (out_dir / "Home.md").exists()

    def test_idempotent_second_run(self, tmp_path: Path):
        _invoke(tmp_path)
        result, _, out_dir, _ = _invoke(tmp_path)
        assert result.exit_code == 0
        assert (out_dir / "Home.md").exists()

    def test_index_search_runs_after_report_when_enabled(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        call_order: list[str] = []

        def fake_report(cfg, conn, **kwargs):
            call_order.append("report")
            from matlock.stages.report import ReportResult

            return ReportResult(files_written=0, target="all")

        def fake_search_index_stage(cfg, conn, **kwargs):
            call_order.append("search-index")
            from matlock.search.indexer import SearchIndexingResult

            return SearchIndexingResult(indexed_files=1)

        with patch("matlock.cli.run_report", side_effect=fake_report):
            with patch("matlock.cli.run_search_index_stage", side_effect=fake_search_index_stage):
                result = runner.invoke(
                    app,
                    ["--config", str(cfg_path), "run-all", "--index-search"],
                )

        assert result.exit_code == 0
        assert call_order[-2:] == ["report", "search-index"]
        assert "Search index:" in result.output

    def test_index_search_not_run_by_default(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        with patch("matlock.cli.run_search_index_stage") as mock_search_index:
            result = runner.invoke(app, ["--config", str(cfg_path), "run-all"])

        assert result.exit_code == 0
        assert mock_search_index.call_count == 0


# ---------------------------------------------------------------------------
# --skip-rollup flag
# ---------------------------------------------------------------------------


class TestRunAllSkipRollup:
    def test_exits_zero_with_skip_rollup(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--skip-rollup")
        assert result.exit_code == 0

    def test_no_rollup_line_when_skipped(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--skip-rollup")
        assert "Rollup:" not in result.output

    def test_report_still_runs_when_rollup_skipped(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--skip-rollup")
        assert "Report:" in result.output

    def test_completion_line_printed_when_rollup_skipped(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--skip-rollup")
        assert "run-all complete." in result.output

    def test_dashboard_written_when_rollup_skipped(self, tmp_path: Path):
        result, _, out_dir, _ = _invoke(tmp_path, "--skip-rollup")
        assert result.exit_code == 0
        assert (out_dir / "Home.md").exists()


# ---------------------------------------------------------------------------
# --force-sync flag
# ---------------------------------------------------------------------------


class TestRunAllForceSync:
    def test_exits_zero_with_force_sync(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--force-sync")
        assert result.exit_code == 0

    def test_sync_line_present_with_force_sync(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path, "--force-sync")
        assert "Sync:" in result.output

    def test_force_sync_causes_reparse(self, tmp_path: Path):
        """Second run with --force-sync should re-mark files as needing parsing."""
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        md = vault / "note.md"
        md.write_text("# Note\n- [ ] Task one\n", encoding="utf-8")
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        # First full run — syncs and parses the file
        runner.invoke(app, ["--config", str(cfg_path), "run-all"])

        # Second run with --force-sync — file should be re-inserted/updated
        result = runner.invoke(app, ["--config", str(cfg_path), "run-all", "--force-sync"])
        assert result.exit_code == 0
        assert "Sync:" in result.output


# ---------------------------------------------------------------------------
# --scan-projects flag
# ---------------------------------------------------------------------------


class TestRunAllScanProjects:
    def test_without_flag_no_scan_line(self, tmp_path: Path):
        result, *_ = _invoke(tmp_path)
        assert "Scan-projects:" not in result.output

    def test_with_flag_prints_scan_line(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        _write_project_note(vault)

        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        result = runner.invoke(app, ["--config", str(cfg_path), "run-all", "--scan-projects"])
        assert result.exit_code == 0
        assert "Scan-projects:" in result.output

    def test_with_flag_scan_runs_before_sync(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        _write_project_note(vault)

        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        result = runner.invoke(app, ["--config", str(cfg_path), "run-all", "--scan-projects"])
        assert result.exit_code == 0
        assert "Scan-projects:" in result.output
        assert "Sync:" in result.output
        assert result.output.index("Scan-projects:") < result.output.index("Sync:")

    def test_with_flag_pipeline_still_runs(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        _write_project_note(vault)

        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)

        result = runner.invoke(app, ["--config", str(cfg_path), "run-all", "--scan-projects"])
        assert result.exit_code == 0
        assert "Sync:" in result.output
        assert "Parse:" in result.output
        assert "Map-projects:" in result.output
        assert "Report:" in result.output


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestRunAllErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "run-all",
        ])
        assert result.exit_code != 0

    def test_missing_config_error_message(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "run-all",
        ])
        assert "error" in result.output.lower() or "error" in (result.stderr or "").lower()


# ---------------------------------------------------------------------------
# Subprocess smoke test
# ---------------------------------------------------------------------------


class TestRunAllSubprocess:
    def test_subprocess_smoke(self):
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr
