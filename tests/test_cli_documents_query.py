from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, init_db
from tests.test_cli_search_query import _make_cfg, seed_search_db

runner = CliRunner()


def test_documents_query_defaults_to_metadata_file_results(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)

    result = runner.invoke(app, ["--config", str(cfg_path), "documents", "query"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 1
    assert payload["returned_matches"] == 1
    assert payload["results"][0]["file_path"] == "Notes/alpha.md"
    assert payload["results"][0]["file_ext"] == ".md"
    assert payload["results"][0]["file_details"]["content"] is None


def test_documents_query_fts_supports_chunk_granularity_and_context(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)

    result = runner.invoke(
        app,
        [
            "--config", str(cfg_path), "documents", "query", "--text", "database",
            "--search-mode", "fts_only", "--granularity", "chunk",
            "--surrounding-chunks", "1",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["results"][0]["chunk_details"]["chunk_id"] == "Notes/alpha.md#0000"
    assert "Database" in payload["results"][0]["chunk_details"]["content"]
    assert payload["results"][0]["file_details"] is None


def test_documents_query_rejects_content_options(tmp_path: Path) -> None:
    cfg_path, _, db_path = _make_cfg(tmp_path)
    db_path.touch()

    for option in ("--include-content", "--no-include-content"):
        result = runner.invoke(app, ["--config", str(cfg_path), "documents", "query", option])

        assert result.exit_code != 0


def test_documents_query_named_filters_and_count_only(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)
    conn = get_connection(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)",
        ("SUPER", "Super", "P1"),
    )
    conn.execute(
        "INSERT INTO project (project_id, super_project_id, title) VALUES (?, ?, ?)",
        ("alpha", "SUPER", "Alpha"),
    )
    conn.execute(
        "INSERT INTO file_project (file_path, project_id) VALUES (?, ?)",
        ("Notes/alpha.md", "alpha"),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(
        app,
        [
            "--config", str(cfg_path), "documents", "query",
            "--file-path", "/Notes/", "--file-ext", ".MD",
            "--attributes", "status:eq:active", "--project-id", "ALPHA",
            "--created", ">=2024-08-09", "--modified", "<2024-08-11",
            "--count-only",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 1
    assert payload["returned_matches"] == 0
    assert payload["results"] == []


def test_documents_query_rejects_invalid_filters(tmp_path: Path) -> None:
    cfg_path, _, db_path = _make_cfg(tmp_path)
    db_path.touch()

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "documents", "query", "--created", "not-a-date"],
    )

    assert result.exit_code == 1
    assert "invalid document filter" in result.stderr


def test_documents_query_rejects_empty_named_filter_values(tmp_path: Path) -> None:
    cfg_path, _, db_path = _make_cfg(tmp_path)
    db_path.touch()

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "documents", "query", "--file-path", ""],
    )

    assert result.exit_code == 1
    assert "values cannot be empty" in result.stderr
