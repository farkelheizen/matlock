from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from matlock2.database import Database, DatabaseError
from matlock2_mcp import PolicyDecision, SearchService
from matlock2_mcp.search import SearchError
from tests.test_ingest import _config, _engine
from tests.test_mcp import service


def _write(path: Path, domain: str, digest: str, key: str, value: Any) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO content_attributes(domain, sha256, attributes_json, updated_at)
               VALUES (?, ?, json_set('{}', ?, json(?)), strftime('%Y-%m-%dT%H:%M:%fZ','now'))
               ON CONFLICT(domain, sha256) DO UPDATE SET
               attributes_json=json_set(content_attributes.attributes_json, ?, json(?)),
               updated_at=excluded.updated_at""",
            (domain, digest, "$." + key, json.dumps(value), "$." + key, json.dumps(value)),
        )


def test_attributes_follow_hash_and_external_writers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    for name in ("a.md", "b.md"):
        (config.vault_path / name).write_text("# Same\nCafé\n", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.scan().ingested == 2
        first = db.get_source("a.md")
        assert first["body_sha256"] == db.get_source("b.md")["body_sha256"]
        digest = first["body_sha256"]
        _write(config.database_path, "document", digest, "first", None)
        _write(config.database_path, "document", digest, "second", {"done": True})
        row = db.connection.execute(
            "SELECT attributes_json, updated_at FROM content_attributes WHERE domain='document' AND sha256=?",
            (digest,),
        ).fetchone()
        assert json.loads(row["attributes_json"]) == {"first": None, "second": {"done": True}}
        assert row["updated_at"].endswith("Z")
        _write(config.database_path, "chunk", digest, "first", 7)
        assert db.connection.execute("SELECT count(*) FROM content_attributes").fetchone()[0] == 2
        (config.vault_path / "a.md").write_text("# Changed\nother", encoding="utf-8")
        assert engine.ingest_path("a.md") == "ingested"
        assert db.get_source("a.md")["body_sha256"] != digest
        assert db.remove_source("b.md")
        assert db.connection.execute(
            "SELECT count(*) FROM content_attributes WHERE domain='document' AND sha256=?", (digest,)
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO content_attributes VALUES ('document', ?, '[]', 'now')", ("a" * 64,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO content_attributes VALUES ('other', ?, '{}', 'now')", ("a" * 64,)
            )
    finally:
        db.close()


def test_concurrent_writers_preserve_independent_values(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with Database(config.database_path, config):
        pass
    gate = threading.Barrier(2)
    errors: list[Exception] = []

    def writer(key: str) -> None:
        try:
            gate.wait(timeout=5)
            for value in range(10):
                _write(config.database_path, "document", "a" * 64, key, value)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(key,)) for key in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors and all(not thread.is_alive() for thread in threads)
    with sqlite3.connect(config.database_path) as db:
        assert json.loads(db.execute("SELECT attributes_json FROM content_attributes").fetchone()[0]) == {
            "first": 9, "second": 9,
        }


def _make_legacy(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX source_documents_body_sha256_idx")
        db.execute("DROP INDEX chunks_content_sha256_idx")
        db.execute("DROP TABLE content_attributes")
        db.execute("ALTER TABLE source_documents DROP COLUMN body")
        db.execute("ALTER TABLE source_documents DROP COLUMN body_sha256")
        db.execute("ALTER TABLE chunks DROP COLUMN content_sha256")
        db.execute("UPDATE database_metadata SET value='1' WHERE key='schema_version'")


@pytest.mark.parametrize("unavailable", ["changed", "missing"])
def test_migration_rolls_back_until_indexed_source_is_available(
    tmp_path: Path, unavailable: str,
) -> None:
    config = _config(tmp_path)
    path = config.vault_path / "note.md"
    original = "---\na: 1\n---\n# Title\ntext\n"
    path.write_text(original, encoding="utf-8")
    db, engine = _engine(config)
    assert engine.ingest_path("note.md") == "ingested"
    old_chunk = db.get_chunks("note.md")[0]["content"]
    db.close()
    _make_legacy(config.database_path)
    if unavailable == "changed":
        path.write_text("changed", encoding="utf-8")
    else:
        path.unlink()
    with pytest.raises(DatabaseError, match="restore"):
        Database(config.database_path, config)
    with sqlite3.connect(config.database_path) as check:
        assert check.execute("SELECT value FROM database_metadata WHERE key='schema_version'").fetchone()[0] == "1"
        assert "body" not in {row[1] for row in check.execute("PRAGMA table_info(source_documents)")}
    path.write_text(original, encoding="utf-8")
    with Database(config.database_path, config) as migrated:
        doc = migrated.get_source("note.md")
        assert doc["body"] == "# Title\ntext\n"
        assert doc["body_sha256"] == hashlib.sha256(doc["body"].encode()).hexdigest()
        chunk = migrated.get_chunks("note.md")[0]
        assert chunk["content"] == old_chunk
        assert migrated.fts_search("Title")
        assert migrated.connection.execute("SELECT count(*) FROM vec_chunks").fetchone()[0] == 1


def _add_attributes(search: SearchService) -> tuple[str, str]:
    with sqlite3.connect(search.config.database_path) as db:
        doc_hash = db.execute(
            "SELECT body_sha256 FROM source_documents WHERE path LIKE '%decision.md'"
        ).fetchone()[0]
        chunk_id, chunk_hash = db.execute(
            "SELECT chunk_id, content_sha256 FROM chunks WHERE source_path LIKE '%decision.md'"
        ).fetchone()
    _write(search.config.database_path, "document", doc_hash, "review", {"ok": True, "count": 3})
    _write(search.config.database_path, "document", doc_hash, "private", "do not expose")
    _write(search.config.database_path, "chunk", chunk_hash, "scan", {"status": "flagged", "value": None})
    return chunk_id, chunk_hash


def test_mcp_attribute_filters_and_exposure(service: SearchService) -> None:
    chunk_id, _ = _add_attributes(service)
    search = SearchService(replace(service.config, document_attribute_keys=("review",),
                                   chunk_attribute_keys=("scan",)), service.embedder)
    result = search.get_chunk(chunk_id)["result"]
    assert result["document_attributes"] == {"review": {"ok": True, "count": 3}}
    assert result["chunk_attributes"]["scan"]["value"] is None
    assert search.metadata_search(document_attribute_filters={"review.count": {"op": "gte", "value": 2}})["results"]
    assert search.full_text_search("migration", chunk_attribute_filters={"scan.status": "flagged"})["results"]
    assert search.vector_search([1.0, 1.0], "test-space",
                                chunk_attribute_filters={"scan.value": {"op": "is_null", "value": True}})["results"]
    assert search.semantic_search("database", chunk_attribute_filters={"scan.status": {"op": "in", "value": ["flagged"]}})["results"]
    assert search.hybrid_knowledge_search("database", "migration",
                                         document_attribute_filters={"review.ok": True})["results"]
    assert search.full_text_search("migration", chunk_attribute_filters={"scan.nope": {"op": "exists", "value": False}})["results"]
    with pytest.raises(ValueError, match="not exposed"):
        search.metadata_search(document_attribute_filters={"private": "do not expose"})
    assert service.get_chunk(chunk_id)["result"]["document_attributes"] == {}


def test_reader_snapshot_and_later_external_write(service: SearchService) -> None:
    chunk_id, digest = _add_attributes(service)
    search = SearchService(replace(service.config, chunk_attribute_keys=("scan",)), service.embedder)
    with search._connection() as reader:
        before = reader.execute(
            "SELECT attributes_json FROM content_attributes WHERE domain='chunk' AND sha256=?", (digest,)
        ).fetchone()[0]
        _write(search.config.database_path, "chunk", digest, "scan", {"status": "updated"})
        assert reader.execute(
            "SELECT attributes_json FROM content_attributes WHERE domain='chunk' AND sha256=?", (digest,)
        ).fetchone()[0] == before
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("DELETE FROM content_attributes")
    assert search.get_chunk(chunk_id)["result"]["chunk_attributes"]["scan"]["status"] == "updated"


@pytest.mark.parametrize(("operator", "value", "expected"), [
    ("eq", 3, 1), ("in", [2, 3], 1), ("lt", 4, 1), ("lte", 3, 1),
    ("gt", 2, 1), ("gte", 3, 1), ("exists", True, 1),
    ("exists", False, 4), ("is_null", True, 0),
])
def test_attribute_filter_operators(
    service: SearchService, operator: str, value: Any, expected: int,
) -> None:
    _add_attributes(service)
    search = SearchService(replace(service.config, document_attribute_keys=("review",)), service.embedder)
    assert len(search.metadata_search(
        document_attribute_filters={"review.count": {"op": operator, "value": value}}
    )["results"]) == expected


class SamplePolicy:
    def rewrite(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "full_text_search" and arguments["keyword_query"] == "alias":
            arguments["keyword_query"] = "migration"
        return arguments

    def document(self, record: dict[str, Any]) -> PolicyDecision:
        attrs = record["attributes"] or {}
        if attrs.get("block") is True:
            return PolicyDecision("hide")
        return PolicyDecision()

    def chunk(self, record: dict[str, Any]) -> PolicyDecision:
        attrs = record["attributes"] or {}
        if attrs.get("redact") is True:
            return PolicyDecision("null", disclose_metadata=False, disclose_attributes=False)
        return PolicyDecision()


def test_policy_is_enforced_before_search_limits_and_lookup(service: SearchService) -> None:
    chunk_id, chunk_hash = _add_attributes(service)
    _write(service.config.database_path, "chunk", chunk_hash, "redact", True)
    search = SearchService(replace(service.config, document_attribute_keys=("review",),
                                   chunk_attribute_keys=("scan",)), service.embedder, SamplePolicy())
    result = search.full_text_search("alias")["results"][0]
    assert result["chunk_id"] == chunk_id and result["content"] is None
    assert result["metadata"] is None and result["chunk_attributes"] is None
    assert search.get_chunk(chunk_id)["result"]["content"] is None
    assert search.full_text_search("migration", chunk_attribute_filters={"scan.status": "flagged"})["results"] == []
    with sqlite3.connect(service.config.database_path) as db:
        doc_hash = db.execute("SELECT body_sha256 FROM source_documents WHERE path LIKE '%decision.md'").fetchone()[0]
    _write(service.config.database_path, "document", doc_hash, "block", True)
    with sqlite3.connect(service.config.database_path) as db:
        db.execute(
            "UPDATE source_documents SET embedding_version='hidden-incompatible' WHERE body_sha256=?",
            (doc_hash,),
        )
    assert search.get_chunk(chunk_id) == {"result": None}
    assert search.full_text_search("migration")["results"] == []
    assert all(not result["source_file"].endswith("decision.md") for result in search.metadata_search()["results"])
    assert all(not result["source_file"].endswith("decision.md") for result in search.vector_search([1., 1.], "test-space")["results"])
    assert all(not result["source_file"].endswith("decision.md") for result in search.semantic_search("database")["results"])
    assert all(not result["source_file"].endswith("decision.md") for result in search.hybrid_knowledge_search("database", "migration")["results"])
    assert "decision" not in search.get_distinct_metadata("content_type")["values"]
    assert len(search.metadata_search(limit=1)["results"]) == 1


def test_null_content_and_hidden_hits_do_not_reduce_visible_window(service: SearchService) -> None:
    with sqlite3.connect(service.config.database_path) as db:
        rows = db.execute(
            "SELECT c.chunk_id, c.content_sha256, c.source_path FROM chunks c ORDER BY c.source_path"
        ).fetchall()
    assert len(rows) >= 3
    _write(service.config.database_path, "chunk", rows[0][1], "hide", True)
    _write(service.config.database_path, "chunk", rows[1][1], "redact", True)

    class Mixed(SamplePolicy):
        def chunk(self, record: dict[str, Any]) -> PolicyDecision:
            if (record["attributes"] or {}).get("hide"):
                return PolicyDecision("hide")
            return super().chunk(record)

    search = SearchService(service.config, service.embedder, Mixed())
    results = search.vector_search([1.0, 1.0], "test-space", limit=2)["results"]
    assert len(results) == 2
    assert all(result["chunk_id"] != rows[0][0] for result in results)
    null_chunk = search.get_chunk(rows[1][0])["result"]
    assert null_chunk["content"] is None and not null_chunk["content_truncated"]


def test_policy_failure_is_not_an_unrestricted_query(service: SearchService) -> None:
    class Broken(SamplePolicy):
        def document(self, record: dict[str, Any]) -> PolicyDecision:
            raise RuntimeError("sensitive content")

    search = SearchService(service.config, service.embedder, Broken())
    with pytest.raises(SearchError, match="policy evaluation failed") as error:
        search.metadata_search()
    assert "sensitive content" not in str(error.value)


def test_rewrite_can_require_private_attribute_without_exposing_it(service: SearchService) -> None:
    chunk_id, _ = _add_attributes(service)

    class PrivateFilter(SamplePolicy):
        def rewrite(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if tool == "full_text_search":
                arguments["document_attribute_filters"] = {"private": "do not expose"}
            return arguments

    search = SearchService(service.config, service.embedder, PrivateFilter())
    assert search.full_text_search("migration")["results"][0]["chunk_id"] == chunk_id
    with pytest.raises(ValueError, match="not exposed"):
        search.full_text_search("migration", document_attribute_filters={"private": "do not expose"})


def test_document_null_decision_inherits_and_missing_can_fail_closed(service: SearchService) -> None:
    chunk_id, _ = _add_attributes(service)

    class Closed(SamplePolicy):
        def document(self, record: dict[str, Any]) -> PolicyDecision:
            if record["attributes"] is None:
                return PolicyDecision("hide")
            return PolicyDecision("null", disclose_source=False, disclose_metadata=False)

    search = SearchService(service.config, service.embedder, Closed())
    result = search.get_chunk(chunk_id)["result"]
    assert result["content"] is None and result["source_file"] is None and result["metadata"] is None
    assert search.get_distinct_metadata("content_type")["values"] == []
    assert search.metadata_search({"project_id": "atlas"})["results"] == []
    assert len(search.metadata_search()["results"]) == 1


def test_mcp_registration_accepts_attribute_filters(service: SearchService) -> None:
    fastmcp = pytest.importorskip("fastmcp")
    from matlock2_mcp.registration import register_tools

    _add_attributes(service)
    search = SearchService(replace(service.config, chunk_attribute_keys=("scan",)), service.embedder)
    server = fastmcp.FastMCP("attributes")
    register_tools(server, search)

    async def exercise() -> None:
        async with fastmcp.Client(server) as client:
            result = await client.call_tool("full_text_search", {
                "keyword_query": "migration", "chunk_attribute_filters": {"scan.status": "flagged"},
            })
            assert result.structured_content["results"][0]["chunk_attributes"]["scan"]["status"] == "flagged"

    asyncio.run(exercise())
