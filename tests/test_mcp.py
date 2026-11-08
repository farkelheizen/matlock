from __future__ import annotations

import asyncio
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from matlock2.config import load_config
from matlock2.database import Database
from matlock2.ingest import IngestionEngine
from matlock2_mcp import SearchConfig, SearchService
from matlock2_mcp.search import SearchError
from tests.test_ingest import TestEmbedder

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "second-brain"


class Embedder(TestEmbedder):
    model_name = "test-model"
    dimensions = 2


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SearchService:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    config = replace(
        load_config(EXAMPLE / "matlock2.toml"),
        database_path=tmp_path / "index.sqlite3",
        embedding_model="test-model",
        embedding_dimensions=2,
    )
    with Database(config.database_path, config) as db:
        report = IngestionEngine(config, db, Embedder()).scan()
        assert report.ingested == 5 and not report.unresolved_errors
    return SearchService(SearchConfig(
        config.database_path, embedding_space="test-space", embedding_model="test-model",
        embedding_version=Embedder.version, embedding_dimensions=2,
    ), Embedder())


def test_search_operations_and_filtered_ranking(service: SearchService) -> None:
    docs = service.metadata_search({"project_id": "atlas"}, limit=1)
    assert len(docs["results"]) == 1 and docs["next_offset"] == 1
    assert service.metadata_search({"project_id": "atlas"}, offset=1)["results"][0]["source_file"].endswith("overview.md")
    fts = service.full_text_search("migration", {"project_id": "atlas"})
    assert len(fts["results"]) == 1 and fts["results"][0]["source_file"].endswith("decision.md")
    assert service.full_text_search("migration", {"project_id": "custom-atlas"})["results"] == []
    assert service.full_text_search('migration" OR "SQLite')["results"] == []
    assert service.get_distinct_metadata("project_id")["values"] == ["atlas", "custom-atlas"]
    chunk_id = fts["results"][0]["chunk_id"]
    assert service.get_chunk(chunk_id)["result"]["chunk_id"] == chunk_id
    assert service.get_chunk("0" * 24)["result"] is None

    vectors = service.vector_search([1.0, 1.0], "test-space", {"project_id": "atlas"}, limit=1)
    assert vectors["results"][0]["metadata"]["project_id"] == "atlas"
    assert vectors["truncated"]
    semantic = service.semantic_search("database", {"project_id": "atlas"})
    assert all(r["metadata"]["project_id"] == "atlas" for r in semantic["results"])
    hybrid = service.hybrid_knowledge_search("database", "migration", {"project_id": "atlas"})
    assert all(r["metadata"]["project_id"] == "atlas" for r in hybrid["results"])
    both = next(r for r in hybrid["results"] if "fts_rank" in r and "vector_rank" in r)
    assert both["relevance_score"] == pytest.approx(
        1 / (60 + both["fts_rank"]) + 1 / (60 + both["vector_rank"])
    )
    assert hybrid["results"][0]["relevance_score"] >= hybrid["results"][-1]["relevance_score"]


def test_validation_and_mixed_model_rejection(service: SearchService, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        service.metadata_search({"field[0]": "x"})
    with pytest.raises(ValueError):
        service.metadata_search({"project_id": None})
    with pytest.raises(ValueError):
        service.vector_search([0, 0], "test-space")
    with pytest.raises(ValueError):
        service.vector_search([float("nan"), 1], "test-space")
    with pytest.raises(ValueError):
        service.vector_search([1e-300, 0], "test-space")
    with pytest.raises(ValueError):
        service.metadata_search({"number": 2**64})
    with pytest.raises(SearchError):
        service.vector_search([1, 1], "wrong")
    with sqlite3.connect(service.config.database_path) as db:
        db.execute("UPDATE source_documents SET embedding_version='old' WHERE path LIKE '%decision.md'")
    with pytest.raises(SearchError, match="incompatible"):
        service.semantic_search("database")
    assert service.full_text_search("migration")["results"]
    missing = SearchService(SearchConfig(tmp_path / "missing.sqlite3"))
    with pytest.raises(SearchError):
        missing.metadata_search()
    assert not (tmp_path / "missing.sqlite3").exists()


def test_schema_provider_and_output_limits(service: SearchService) -> None:
    truncated = SearchService(replace(service.config, max_content_chars=5), service.embedder)
    result = truncated.full_text_search("migration")["results"][0]
    assert len(result["content"]) == 5 and result["content_truncated"]
    with pytest.raises(ValueError):
        service.full_text_search("migration", limit=service.config.max_limit + 1)
    assert service.get_distinct_metadata("nested.invalid")["values"] == []
    with pytest.raises(ValueError):
        service.get_distinct_metadata("x[$.bad]")
    wrong_provider = SearchService(replace(service.config, embedding_model="other"), service.embedder)
    with pytest.raises(SearchError, match="provider"):
        wrong_provider.semantic_search("database")
    with pytest.raises(ValueError, match="query"):
        service.semantic_search("")
    with sqlite3.connect(service.config.database_path) as db:
        db.execute("UPDATE database_metadata SET value='99' WHERE key='schema_version'")
    with pytest.raises(SearchError, match="schema"):
        service.metadata_search()


def test_metadata_operators_and_snapshot(service: SearchService) -> None:
    with sqlite3.connect(service.config.database_path) as db:
        db.execute(
            "UPDATE source_documents SET metadata_json=json_set(metadata_json, '$.nested.count', 3, "
            "'$.flag', json('true'), '$.empty', json('null')) WHERE path LIKE '%decision.md'"
        )
    assert len(service.metadata_search({"nested.count": {"op": "gte", "value": 2}})["results"]) == 1
    assert len(service.metadata_search({"flag": True})["results"]) == 1
    assert len(service.metadata_search({"flag": 1})["results"]) == 0
    assert len(service.metadata_search({"empty": {"op": "is_null", "value": True}})["results"]) == 1
    assert len(service.metadata_search({"empty": {"op": "exists", "value": False}})["results"]) == 4
    assert len(service.metadata_search({"project_id": {"op": "in", "value": ["atlas", "custom-atlas"]}})["results"]) == 3
    with service._connection() as reader:
        before = reader.execute("SELECT count(*) FROM source_documents").fetchone()[0]
        with sqlite3.connect(service.config.database_path) as writer:
            writer.execute("DELETE FROM source_documents WHERE path LIKE '%decision.md'")
        assert reader.execute("SELECT count(*) FROM source_documents").fetchone()[0] == before
    assert len(service.metadata_search()["results"]) == before - 1


def test_registration_and_mcp_client(service: SearchService) -> None:
    fastmcp = pytest.importorskip("fastmcp")
    from matlock2_mcp.registration import register_tools

    server = fastmcp.FastMCP("test")
    register_tools(server, service)

    async def exercise() -> None:
        async with fastmcp.Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert names == {"metadata_search", "full_text_search", "semantic_search", "vector_search",
                             "hybrid_knowledge_search", "get_distinct_metadata", "get_chunk"}
            result = await client.call_tool("hybrid_knowledge_search", {
                "semantic_query": "database", "keyword_query": "migration",
                "metadata_filters": {"project_id": "atlas"}, "limit": 5,
            })
            assert result.structured_content["results"]
            values = await client.call_tool("get_distinct_metadata", {"field": "project_id"})
            assert "atlas" in values.structured_content["values"]
    asyncio.run(exercise())

    limited = fastmcp.FastMCP("limited")
    register_tools(limited, service, {"get_chunk"})
    async def check_limited() -> None:
        async with fastmcp.Client(limited) as client:
            assert [tool.name for tool in await client.list_tools()] == ["get_chunk"]
    asyncio.run(check_limited())
    with pytest.raises(ValueError, match="Unknown"):
        register_tools(limited, service, {"unknown"})


def test_example_stdio_host(service: SearchService) -> None:
    fastmcp = pytest.importorskip("fastmcp")
    from fastmcp.client.transports import StdioTransport

    script = EXAMPLE.parent / "mcp_server" / "server.py"
    transport = StdioTransport(command=sys.executable, args=[str(script), "--database", str(service.config.database_path)])

    async def exercise() -> None:
        async with fastmcp.Client(transport) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "full_text_search" in names and "semantic_search" not in names
            result = await client.call_tool("full_text_search", {"keyword_query": "migration"})
            assert result.structured_content["results"][0]["source_file"].endswith("decision.md")
    asyncio.run(exercise())
