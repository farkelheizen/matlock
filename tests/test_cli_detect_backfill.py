from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.stages.detect_backfill import DetectBackfillResult

runner = CliRunner()


def _write_config(path: Path, base_dir: Path, db_path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            {
                "base_directory": str(base_dir),
                "db_path": str(db_path),
                "output_directory": str(base_dir / "_output"),
            },
            fh,
        )


def test_detect_backfill_help_lists_retry_errors_option():
    result = runner.invoke(app, ["detect-backfill", "--help"])

    assert result.exit_code == 0
    assert "--retry-errors" in result.stdout


def test_detect_backfill_passes_retry_option_and_prints_summary(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, tmp_path / "matlock.db")
    received: dict[str, bool] = {}

    def run_backfill(_config, _conn, *, retry_errors: bool) -> DetectBackfillResult:
        received["retry_errors"] = retry_errors
        return DetectBackfillResult(scanned=3, skipped=1, retried=2, errors=1)

    monkeypatch.setattr("matlock.cli.run_detect_backfill", run_backfill)

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "detect-backfill", "--retry-errors"],
    )

    assert result.exit_code == 0
    assert received == {"retry_errors": True}
    assert result.stdout == "Secret detection backfill complete: 3 scanned, 1 skipped, 2 retried, 1 errors\n"
