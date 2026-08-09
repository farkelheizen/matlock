from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from matlock.cli import app
from matlock.search.query_cli import SearchCliOutcome, build_error_response
from matlock.search.embedding import EmbeddingProviderError
from tests.test_cli_search_query import _make_cfg, seed_search_db

runner = CliRunner()


def test_stdio_success_emits_json_only_to_stdout(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "search", "query", "--stdio"],
        input='{"query": "database", "search_mode": "fts_only"}',
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["stats"]["search_mode_executed"] == "fts_only"
    assert payload["results"][0]["file_path"] == "Notes/alpha.md"


def test_stdio_invalid_json_returns_structured_error_payload(tmp_path: Path) -> None:
    cfg_path, _, db_path = _make_cfg(tmp_path)
    db_path.touch()

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "search", "query", "--stdio"],
        input="not-json",
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "error",
        "stats": {
            "total_matches": 0,
            "returned_matches": 0,
            "query_time_ms": 0.0,
            "search_mode_executed": "metadata_only",
        },
        "results": [],
        "error": {
            "code": "input_error",
            "message": "stdin request body is not valid JSON",
            "details": None,
        },
    }


def test_stdio_missing_database_returns_exit_code_two(tmp_path: Path) -> None:
    cfg_path, _, _ = _make_cfg(tmp_path)

    result = runner.invoke(
        app,
        ["--config", str(cfg_path), "search", "query", "--stdio"],
        input='{"query": "database", "search_mode": "fts_only"}',
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "database_error"
    assert "search database file not found" in payload["error"]["message"]


def test_stdio_embedding_failures_map_to_exit_code_three(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)

    with patch(
        "matlock.search.query_cli.SearchQueryEngine.execute",
        side_effect=EmbeddingProviderError("provider offline"),
    ):
        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "search", "query", "--stdio"],
            input='{"query": "database", "search_mode": "hybrid"}',
        )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "embedding_error"
    assert payload["error"]["message"] == "provider offline"


def test_stdio_logs_do_not_contaminate_stdout(tmp_path: Path) -> None:
    cfg_path, vault, db_path = _make_cfg(tmp_path)
    seed_search_db(db_path, vault)

    def fake_run_search_request(*args, **kwargs):
        logging.getLogger("matlock.search.test").warning("warning should stay out of stdout")
        return SearchCliOutcome(
            response=build_error_response(
                error_code="success-placeholder",
                message="placeholder",
            ).model_copy(update={"status": "success", "error": None}),
            exit_code=0,
        )

    with patch("matlock.cli.run_search_request", side_effect=fake_run_search_request):
        result = runner.invoke(
            app,
            ["--config", str(cfg_path), "search", "query", "--stdio"],
            input='{"query": "database", "search_mode": "fts_only"}',
        )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == "success"