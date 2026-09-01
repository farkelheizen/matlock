from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db, replace_search_chunks, upsert_file

runner = CliRunner()


def _write_config(path: Path, base_dir: Path, db_path: Path, out_dir: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(out_dir),
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _make_cfg(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "matlock.db"
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, vault, db_path, tmp_path / "_Matlock")
    return cfg_path, vault, db_path


def seed_search_db(db_path: Path, base_dir: Path) -> None:
    conn = get_connection(db_path)
    init_db(conn)
    file_path = "Notes/alpha.md"
    abs_path = base_dir / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text("# Alpha\n\nDatabase optimization notes\n", encoding="utf-8")
    upsert_file(
        conn,
        {
            "file_path": file_path,
            "sha256": "sha-alpha",
            "file_ext": ".md",
            "created": 1723200000,
            "modified": 1723286400,
            "modified_date": "2026-08-10T00:00:00Z",
            "deleted": 0,
            "length": 36,
            "word_count": 4,
            "meta_data": json.dumps({"status": "active"}),
            "is_generated": 0,
            "needs_parsing": 0,
        },
    )
    replace_search_chunks(
        conn,
        file_path,
        [
            {
                "chunk_id": f"{file_path}#0000",
                "chunk_index": 0,
                "content": "Database optimization notes",
            }
        ],
    )
    conn.commit()
    conn.close()


class TestSearchQueryHelp:
    def test_search_query_help_lists_stdio_option(self):
        result = runner.invoke(app, ["search", "query", "--help"])

        assert result.exit_code == 0
        assert "--stdio" in result.stdout
        assert "--search-mode" in result.stdout
        assert "--min-score" in result.stdout


class TestSearchQueryHumanMode:
    def test_requires_query_text_without_stdio(self, tmp_path: Path):
        cfg_path, _, db_path = _make_cfg(tmp_path)
        db_path.touch()

        result = runner.invoke(app, ["--config", str(cfg_path), "search", "query"])

        assert result.exit_code == 1
        assert "query text is required" in result.stderr

    def test_human_mode_prints_ranked_results(self, tmp_path: Path):
        cfg_path, vault, db_path = _make_cfg(tmp_path)
        seed_search_db(db_path, vault)

        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg_path),
                "search",
                "query",
                "database",
                "--search-mode",
                "fts_only",
            ],
        )

        assert result.exit_code == 0
        assert "Search mode: fts_only" in result.stdout
        assert "Notes/alpha.md" in result.stdout
        assert "Database optimization notes" in result.stdout

    def test_human_mode_respects_file_granularity(self, tmp_path: Path):
        cfg_path, vault, db_path = _make_cfg(tmp_path)
        seed_search_db(db_path, vault)

        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg_path),
                "search",
                "query",
                "database",
                "--search-mode",
                "fts_only",
                "--granularity",
                "file",
            ],
        )

        assert result.exit_code == 0
        assert "Notes/alpha.md" in result.stdout

    def test_human_mode_min_score_filters_results(self, tmp_path: Path):
        cfg_path, vault, db_path = _make_cfg(tmp_path)
        seed_search_db(db_path, vault)

        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg_path),
                "search",
                "query",
                "database",
                "--search-mode",
                "fts_only",
                "--min-score",
                "2.0",
            ],
        )

        assert result.exit_code == 0
        assert "No matches." in result.stdout

    def test_human_mode_redacts_secret_file_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_path, vault, db_path = _make_cfg(tmp_path)
        seed_search_db(db_path, vault)

        conn = get_connection(db_path)
        conn.execute(
            "UPDATE file SET has_secrets = 1 WHERE file_path = ?",
            ("Notes/alpha.md",),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            "matlock.search.query_engine.get_redacted_document",
            lambda _path, _cache_dir: "[REDACTED]",
        )

        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg_path),
                "search",
                "query",
                "database",
                "--search-mode",
                "fts_only",
            ],
        )

        assert result.exit_code == 0
        assert "[REDACTED]" in result.stdout
        assert "Database optimization notes" not in result.stdout