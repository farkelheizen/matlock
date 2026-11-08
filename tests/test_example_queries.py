from __future__ import annotations

import runpy
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from matlock2.config import load_config
from matlock2.database import Database
from matlock2.ingest import IngestionEngine
from tests.test_ingest import TestEmbedder

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "second-brain"


def test_query_example_against_fictional_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    config = replace(
        load_config(EXAMPLE / "matlock2.toml"),
        database_path=tmp_path / "index.sqlite3",
        embedding_dimensions=2,
    )
    with Database(config.database_path, config) as db:
        report = IngestionEngine(config, db, TestEmbedder()).scan()
        assert report.ingested == 5
        assert report.unresolved_errors == 0

    class QueryEmbedder(TestEmbedder):
        def __init__(self, model_name: str, dimensions: int, max_input_tokens: int) -> None:
            assert model_name == config.embedding_model
            assert dimensions == 2
            assert max_input_tokens == 512

    # Exercise the actual script while using the same deterministic test embedder.
    namespace = runpy.run_path(str(EXAMPLE / "query.py"))
    main = namespace["main"]
    monkeypatch.setitem(main.__globals__, "FastEmbedder", QueryEmbedder)
    monkeypatch.setattr(sys, "argv", ["query.py", "--database", str(config.database_path)])
    main()
    output = capsys.readouterr().out
    metadata, remainder = output.split("\nFTS: migration\n")
    fts, vector = remainder.split("\nVector: local database decision\n")
    assert "projects/atlas/decision.md" in metadata
    assert "projects/atlas/overview.md" in metadata
    assert "projects/atlas/retrospective.md" not in metadata
    assert "projects/atlas/decision.md" in fts
    assert "bm25=" in fts
    assert vector.count("cosine distance=") == 5

    with sqlite3.connect(config.database_path) as db:
        db.execute(
            "UPDATE source_documents SET embedding_version = ? WHERE path = ?",
            ("different-version", "projects/atlas/decision.md"),
        )
    with pytest.raises(RuntimeError, match="requires one indexed model"):
        main()


def test_query_example_does_not_create_missing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(sys, "argv", ["query.py", "--database", str(database)])
    main = runpy.run_path(str(EXAMPLE / "query.py"))["main"]
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        main()
    assert not database.exists()
