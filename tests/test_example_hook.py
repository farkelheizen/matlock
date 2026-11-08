from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from matlock2.config import load_config
from matlock2.database import Database
from matlock2.ingest import IngestionEngine
from matlock2.models import DocumentContext
from tests.test_ingest import TestEmbedder

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "second-brain"


@pytest.mark.parametrize(
    ("path", "original", "expected"),
    [
        ("projects/atlas/note.md", {}, {"project_id": "atlas"}),
        ("projects/atlas/note.md", {"project_id": None}, {"project_id": "atlas"}),
        ("projects/atlas/note.md", {"project_id": "other"}, {"project_id": "other"}),
        ("projects/atlas/note.md", {"project_id": ""}, {"project_id": ""}),
        ("projects/atlas/note.md", {"project_id": False}, {"project_id": False}),
        ("projects/atlas/note.MD", {}, {"project_id": "atlas"}),
        ("projects/atlas/nested/note.md", {}, {}),
        ("other/projects/atlas/note.md", {}, {}),
        ("projects/atlas/note.txt", {}, {}),
        ("projects/atlas", {}, {}),
        ("projects//note.md", {}, {}),
    ],
)
def test_example_project_hook(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    original: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    hook = importlib.import_module("project_metadata").project_id_from_path
    metadata = dict(original)
    assert hook(DocumentContext(path, "body"), metadata) is None
    assert metadata == expected


def test_example_hook_indexes_frontmatter_without_changing_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    vault = tmp_path / "vault"
    project = vault / "projects" / "atlas"
    project.mkdir(parents=True)
    contents = {
        "missing.md": "# Missing\nbody\n",
        "null.md": "---\nproject_id: null\n---\n# Null\nbody\n",
        "explicit.md": "---\nproject_id: external\n---\n# Explicit\nbody\n",
    }
    for name, content in contents.items():
        (project / name).write_text(content, encoding="utf-8")

    config = replace(
        load_config(EXAMPLE / "matlock2.toml"),
        vault_path=vault,
        database_path=tmp_path / "index.sqlite3",
        embedding_dimensions=2,
    )
    with Database(config.database_path, config) as database:
        engine = IngestionEngine(config, database, TestEmbedder())
        report = engine.scan()
        assert report.ingested == 3
        assert report.unresolved_errors == 0
        for name, expected in {
            "missing.md": "atlas",
            "null.md": "atlas",
            "explicit.md": "external",
        }.items():
            row = database.get_source(f"projects/atlas/{name}")
            assert row is not None
            assert json.loads(row["metadata_json"])["project_id"] == expected
            assert (project / name).read_text(encoding="utf-8") == contents[name]
