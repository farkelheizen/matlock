"""Tests for matlock/cli.py — server subcommand."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from matlock.cli import app

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


def _invoke_help():
    return runner.invoke(app, ["server", "--help"])


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestServerHelp:
    def test_server_help_exits_zero(self):
        result = _invoke_help()
        assert result.exit_code == 0

    def test_server_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "server" in result.output

    def test_debounce_option_in_help(self):
        result = _invoke_help()
        assert "--debounce" in result.output

    def test_scan_projects_option_in_help(self):
        result = _invoke_help()
        assert "--scan-projects" in result.output

    def test_skip_rollup_option_in_help(self):
        result = _invoke_help()
        assert "--skip-rollup" in result.output

    def test_force_sync_option_in_help(self):
        result = _invoke_help()
        assert "--force-sync" in result.output

    def test_force_rollup_option_in_help(self):
        result = _invoke_help()
        assert "--force-rollup" in result.output

    def test_force_report_option_in_help(self):
        result = _invoke_help()
        assert "--force-report" in result.output


# ---------------------------------------------------------------------------
# New flag pass-through tests
# ---------------------------------------------------------------------------


class TestServerNewFlags:
    def _cfg(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)
        return cfg_path

    def test_force_sync_passed_to_run_server(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, skip_rollup=False,
                            force_sync=False, force_rollup=False, force_report=False):
            calls.append({"force_sync": force_sync})

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server", "--force-sync"])

        assert result.exit_code == 0
        assert calls[0]["force_sync"] is True

    def test_force_rollup_passed_to_run_server(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, skip_rollup=False,
                            force_sync=False, force_rollup=False, force_report=False):
            calls.append({"force_rollup": force_rollup})

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server", "--force-rollup"])

        assert result.exit_code == 0
        assert calls[0]["force_rollup"] is True

    def test_force_report_passed_to_run_server(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, skip_rollup=False,
                            force_sync=False, force_rollup=False, force_report=False):
            calls.append({"force_report": force_report})

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server", "--force-report"])

        assert result.exit_code == 0
        assert calls[0]["force_report"] is True

    def test_skip_rollup_passed_to_run_server(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, skip_rollup=False,
                            force_sync=False, force_rollup=False, force_report=False):
            calls.append({"skip_rollup": skip_rollup})

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server", "--skip-rollup"])

        assert result.exit_code == 0
        assert calls[0]["skip_rollup"] is True

    def test_scan_projects_runs_merge_then_reloads(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        run_server_calls: list = []

        def fake_run_server(cfg, **kwargs):
            run_server_calls.append(cfg)

        from matlock.stages.scan_projects import MergeResult

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            with patch("matlock.stages.scan_projects.scan_vault", return_value=([], [])):
                with patch("matlock.stages.scan_projects.merge_into_config",
                           return_value=MergeResult(
                               super_projects_added=0, super_projects_updated=0,
                               super_projects_deleted=0, projects_added=0,
                               projects_updated=0, projects_deleted=0,
                               backup_path=None,
                           )) as mock_merge:
                    result = runner.invoke(
                        app,
                        ["--config", str(cfg_path), "server", "--scan-projects"],
                    )

        assert result.exit_code == 0
        assert mock_merge.call_count == 1
        assert len(run_server_calls) == 1

    def test_no_flags_defaults_are_false(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, skip_rollup=False,
                            force_sync=False, force_rollup=False, force_report=False):
            calls.append({
                "skip_rollup": skip_rollup,
                "force_sync": force_sync,
                "force_rollup": force_rollup,
                "force_report": force_report,
            })

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server"])

        assert result.exit_code == 0
        assert calls[0] == {
            "skip_rollup": False,
            "force_sync": False,
            "force_rollup": False,
            "force_report": False,
        }


# ---------------------------------------------------------------------------
# Subprocess smoke
# ---------------------------------------------------------------------------


class TestServerSubprocess:
    def test_subprocess_smoke(self):
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr


class TestServerErrors:
    def test_missing_config_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "server",
        ])
        assert result.exit_code != 0

    def test_missing_config_error_message(self, tmp_path: Path):
        result = runner.invoke(app, [
            "--config", str(tmp_path / "nonexistent.yaml"),
            "server",
        ])
        combined = result.output + (result.stderr or "")
        assert "error" in combined.lower()


# ---------------------------------------------------------------------------
# Behaviour with mocked run_server
# ---------------------------------------------------------------------------


class TestServerCommand:
    def _cfg(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        out_dir = tmp_path / "_Matlock"
        cfg_path = tmp_path / "config.yaml"
        db_path = tmp_path / "matlock.db"
        _write_config(cfg_path, vault, db_path, out_dir)
        return cfg_path

    def test_calls_run_server(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, **kwargs):
            calls.append({"cfg": cfg, "debounce": debounce_seconds})

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            result = runner.invoke(app, ["--config", str(cfg_path), "server"])

        assert result.exit_code == 0
        assert len(calls) == 1

    def test_default_debounce_is_none(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, **kwargs):
            calls.append(debounce_seconds)

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            runner.invoke(app, ["--config", str(cfg_path), "server"])

        assert calls[0] is None

    def test_debounce_value_passed_through(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        calls: list = []

        def fake_run_server(cfg, debounce_seconds=None, **kwargs):
            calls.append(debounce_seconds)

        with patch("matlock.cli.run_server", side_effect=fake_run_server):
            runner.invoke(app, ["--config", str(cfg_path), "server", "--debounce", "15"])

        assert calls[0] == 15

    def test_exits_zero_after_run_server_returns(self, tmp_path: Path):
        cfg_path = self._cfg(tmp_path)
        with patch("matlock.cli.run_server", return_value=None):
            result = runner.invoke(app, ["--config", str(cfg_path), "server"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Subprocess smoke
# ---------------------------------------------------------------------------


class TestServerSubprocess:
    def test_subprocess_smoke(self):
        proc = subprocess.run(
            [sys.executable, "-m", "matlock.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Traceback" not in proc.stderr
