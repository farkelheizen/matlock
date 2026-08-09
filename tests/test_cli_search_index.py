"""Tests for the matlock search index CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.search.indexer import SearchIndexingResult

runner = CliRunner()


def _write_config(path: Path, base_dir: Path, db_path: Path, out_dir: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(out_dir),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _make_cfg(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, tmp_path / "matlock.db", tmp_path / "_Matlock")
    return cfg_path


class TestSearchIndexHelp:
    def test_search_group_appears_in_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "search" in result.output

    def test_search_index_help_lists_options(self):
        result = runner.invoke(app, ["search", "index", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output
        assert "--batch-size" in result.output
        assert "--model" in result.output


class TestSearchIndexCommand:
    def test_calls_stage_with_defaults(self, tmp_path: Path):
        cfg_path = _make_cfg(tmp_path)
        calls: list[dict[str, object]] = []

        def fake_run_search_index_stage(cfg, conn, **kwargs):
            calls.append(kwargs)
            return SearchIndexingResult(indexed_files=1, chunks_written=2, vectors_written=2)

        with patch("matlock.cli.run_search_index_stage", side_effect=fake_run_search_index_stage):
            result = runner.invoke(app, ["--config", str(cfg_path), "search", "index"])

        assert result.exit_code == 0
        assert calls == [{"force": False, "batch_size": None, "model_name": None}]
        assert "Search index complete:" in result.output

    def test_passes_force_batch_size_and_model_overrides(self, tmp_path: Path):
        cfg_path = _make_cfg(tmp_path)
        calls: list[dict[str, object]] = []

        def fake_run_search_index_stage(cfg, conn, **kwargs):
            calls.append(kwargs)
            return SearchIndexingResult()

        with patch("matlock.cli.run_search_index_stage", side_effect=fake_run_search_index_stage):
            result = runner.invoke(
                app,
                [
                    "--config",
                    str(cfg_path),
                    "search",
                    "index",
                    "--force",
                    "--batch-size",
                    "17",
                    "--model",
                    "test-model",
                ],
            )

        assert result.exit_code == 0
        assert calls == [{"force": True, "batch_size": 17, "model_name": "test-model"}]