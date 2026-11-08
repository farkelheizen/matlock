from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator
from unittest.mock import patch

import pytest

from matlock2.chunking import chunk_markdown
from matlock2.cli import main
from matlock2.config import ConfigurationError, load_config
from matlock2.database import Database, DatabaseError
from matlock2.ingest import IngestionEngine
from matlock2.markdown import MarkdownParseError, parse_markdown
from matlock2.models import Chunk, IngestConfig
from matlock2.watcher import _PendingEvent, _VaultEventHandler
from matlock2.watcher import VaultWatcher
from watchdog.events import FileMovedEvent, FileModifiedEvent


class WordTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())


class TestEmbedder(WordTokenizer):
    version = "test-embedder-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(self.count(text)), 1.0] for text in texts]


class CharacterTokenizer:
    def count(self, text: str) -> int:
        return len(text)


def add_metadata(context: object, metadata: dict[str, object]) -> None:
    metadata["path_seen"] = getattr(context, "relative_path")


def fail_hook(context: object, metadata: dict[str, object]) -> None:
    raise ValueError(f"hook failed for {getattr(context, 'relative_path')}")


def _config(tmp_path: Path, *, hooks: tuple[str, ...] = ()) -> IngestConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return IngestConfig(
        vault_path=vault,
        database_path=tmp_path / "index.sqlite3",
        target_tokens=10,
        overlap_tokens=2,
        embedding_dimensions=2,
        max_input_tokens=20,
        hooks=hooks,
    )


def _engine(config: IngestConfig) -> tuple[Database, IngestionEngine]:
    db = Database(config.database_path, config)
    return db, IngestionEngine(config, db, TestEmbedder())


def test_frontmatter_is_parsed_to_json_compatible_metadata() -> None:
    parsed = parse_markdown(
        "projects/alpha/decision.md",
        "---\ntitle: Decision\ndate: 2026-11-08\nlabels: [one, two]\n---\n# Body\n",
    )
    assert parsed.metadata == {
        "title": "Decision",
        "date": "2026-11-08",
        "labels": ["one", "two"],
    }
    assert parsed.body == "# Body\n"


@pytest.mark.parametrize(
    "source",
    ["---\ntitle: [\n---\nbody", "---\ntitle: test\nbody", "---\n- a\n---\n"],
)
def test_invalid_frontmatter_is_reported(source: str) -> None:
    with pytest.raises(MarkdownParseError):
        parse_markdown("bad.md", source)


def test_markdown_without_frontmatter_is_supported() -> None:
    assert parse_markdown("plain.md", "# Plain\ntext").metadata == {}


def test_heading_aware_chunking_preserves_breadcrumbs_and_respects_limits() -> None:
    tokenizer = WordTokenizer()
    chunks = chunk_markdown(
        "projects/alpha/note.md",
        "# Project Alpha\n" + " ".join(f"intro{i}" for i in range(9))
        + "\n## Decision\n"
        + " ".join(f"word{i}" for i in range(45)),
        tokenizer,
        target_tokens=10,
        overlap_tokens=2,
        max_input_tokens=12,
    )
    assert len(chunks) > 2
    assert all(tokenizer.count(chunk.content) <= 10 for chunk in chunks)
    assert any("## Decision" in chunk.content for chunk in chunks)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    indexed_words = " ".join(chunk.content for chunk in chunks)
    assert all(f"word{index}" in indexed_words for index in range(45))


def test_headings_inside_fenced_code_are_not_section_boundaries() -> None:
    chunks = chunk_markdown(
        "note.md",
        "# Real heading\n```markdown\n# Not a heading\n```\n"
        "### Nested heading\nnested body\n",
        WordTokenizer(),
        target_tokens=100,
        overlap_tokens=5,
        max_input_tokens=110,
    )
    assert len(chunks) == 2
    assert "# Not a heading" in chunks[0].content
    assert "# Real heading\n### Nested heading" in chunks[1].content


def test_chunk_overlap_repeats_boundary_tokens() -> None:
    tokenizer = WordTokenizer()
    chunks = chunk_markdown(
        "note.md",
        " ".join(f"token{index}" for index in range(35)),
        tokenizer,
        target_tokens=10,
        overlap_tokens=2,
        max_input_tokens=10,
    )
    assert len(chunks) > 2
    for previous, current in zip(chunks, chunks[1:], strict=False):
        previous_tokens = set(previous.content.split())
        current_tokens = set(current.content.split())
        assert previous_tokens & current_tokens


def test_unbroken_oversized_text_does_not_create_pathological_overlap() -> None:
    chunks = chunk_markdown(
        "note.md",
        "x" * 100,
        CharacterTokenizer(),
        target_tokens=10,
        overlap_tokens=2,
        max_input_tokens=10,
    )
    assert len(chunks) == 10
    assert all(len(chunk.content) <= 10 for chunk in chunks)


def test_scan_hashes_idempotency_fts_and_vector_persistence(tmp_path: Path) -> None:
    config = _config(tmp_path, hooks=("matlock2.hooks:project_id_from_path",))
    path = config.vault_path / "projects" / "alpha" / "decision.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nstatus: accepted\n---\n# Decision\nWe selected a reliable storage design.\n",
        encoding="utf-8",
    )
    db, engine = _engine(config)
    try:
        assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        first = engine.scan()
        assert first.discovered == 1
        assert first.ingested == 1
        assert first.unresolved_errors == 0
        stored = db.get_source("projects/alpha/decision.md")
        assert stored is not None
        assert stored["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert json.loads(stored["metadata_json"]) == {
            "status": "accepted",
            "project_id": "alpha",
        }
        assert db.fts_search("reliable")
        assert db.connection.execute("SELECT count(*) FROM vec_chunks").fetchone()[0] > 0
        assert db.distinct_metadata_values("status") == ["accepted"]

        second = engine.scan()
        assert second.skipped == 1
        assert second.ingested == 0
    finally:
        db.close()


def test_content_hash_not_mtime_or_size_detects_changes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = config.vault_path / "note.md"
    path.write_text("# Note\nalpha\n", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        original_stat = path.stat()
        original_hash = db.get_source("note.md")["sha256"]

        os.utime(
            path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
        )
        report = engine.scan()
        assert report.skipped == 1

        path.write_text("# Note\nbravo\n", encoding="utf-8")
        os.utime(
            path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        assert path.stat().st_size == original_stat.st_size
        changed = engine.scan()
        assert changed.ingested == 1
        assert db.get_source("note.md")["sha256"] != original_hash
        assert db.fts_search("bravo")
        assert db.fts_search("alpha") == []
    finally:
        db.close()


def test_processing_fingerprint_reingests_unchanged_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.vault_path / "note.md").write_text("# Note\nsame content", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        changed_pipeline = replace(config, target_tokens=9)
        changed_engine = IngestionEngine(
            changed_pipeline, db, TestEmbedder()
        )
        result = changed_engine.ingest_path("note.md")
        assert result == "ingested"
        stored = db.get_source("note.md")
        assert stored is not None
        assert stored["sha256"] == hashlib.sha256(
            (config.vault_path / "note.md").read_bytes()
        ).hexdigest()
        assert stored["processing_fingerprint"] == changed_engine.processing_fingerprint
    finally:
        db.close()


def test_failed_new_version_keeps_last_good_index_and_resolves_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = config.vault_path / "note.md"
    path.write_text("# Old\npreviously searchable content\n", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        old_source = db.get_source("note.md")
        old_chunks = [row["content"] for row in db.get_chunks("note.md")]
        path.write_text("# New\nreplacement content\n", encoding="utf-8")

        class FailingEmbedder(TestEmbedder):
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("test embedding failure")

        failed_engine = IngestionEngine(
            config,
            db,
            FailingEmbedder(),
            FailingEmbedder(),
        )
        failure = failed_engine.ingest_path("note.md")
        assert not isinstance(failure, str)
        assert db.get_source("note.md")["sha256"] == old_source["sha256"]
        assert [row["content"] for row in db.get_chunks("note.md")] == old_chunks
        assert db.unresolved_error_count() == 1

        assert engine.ingest_path("note.md") == "ingested"
        assert db.unresolved_error_count() == 0
        errors = db.connection.execute(
            "SELECT status, attempt_count FROM ingestion_errors"
        ).fetchall()
        assert [(row["status"], row["attempt_count"]) for row in errors] == [
            ("resolved", 1)
        ]
    finally:
        db.close()


def test_custom_hook_mutates_metadata_and_hook_failure_is_recorded(tmp_path: Path) -> None:
    config = _config(tmp_path, hooks=("tests.test_ingest:add_metadata",))
    (config.vault_path / "note.md").write_text("# Note\nbody", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        row = db.get_source("note.md")
        assert row is not None
        assert json.loads(row["metadata_json"]) == {"path_seen": "note.md"}
    finally:
        db.close()

    failed_config = _config(tmp_path / "failed", hooks=("tests.test_ingest:fail_hook",))
    (failed_config.vault_path / "note.md").write_text("# Note\nbody", encoding="utf-8")
    db, failed_engine = _engine(failed_config)
    try:
        failure = failed_engine.ingest_path("note.md")
        assert not isinstance(failure, str)
        assert failure.stage == "hooks"
        assert "details omitted" in failure.error
        assert "note" not in failure.error
        assert db.unresolved_error_count() == 1
        assert db.get_source("note.md") is None
    finally:
        db.close()


def test_later_stage_failure_resolves_obsolete_same_hash_error(tmp_path: Path) -> None:
    config = _config(tmp_path, hooks=("tests.test_ingest:fail_hook",))
    (config.vault_path / "note.md").write_text("# Note\nbody", encoding="utf-8")
    db, engine = _engine(config)
    try:
        with patch("matlock2.ingest.parse_markdown", side_effect=ValueError("temporary")):
            parse_failure = engine.ingest_path("note.md")
        assert not isinstance(parse_failure, str)
        assert parse_failure.stage == "parse"

        hook_failure = engine.ingest_path("note.md")
        assert not isinstance(hook_failure, str)
        assert hook_failure.stage == "hooks"
        states = db.connection.execute(
            "SELECT stage, status FROM ingestion_errors ORDER BY stage"
        ).fetchall()
        assert [(row["stage"], row["status"]) for row in states] == [
            ("hooks", "unresolved"),
            ("parse", "resolved"),
        ]
    finally:
        db.close()


def test_empty_markdown_ingests_without_calling_embedder(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.vault_path / "empty.md").write_text("", encoding="utf-8")
    db = Database(config.database_path, config)

    class NoEmptyEmbedder(TestEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            assert texts
            return super().embed(texts)

    engine = IngestionEngine(config, db, NoEmptyEmbedder())
    try:
        assert engine.ingest_path("empty.md") == "ingested"
        assert db.get_source("empty.md") is not None
        assert db.get_chunks("empty.md") == []
    finally:
        db.close()


def test_repeated_failures_increment_persisted_error_attempts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.vault_path / "bad.md").write_bytes(b"---\ninvalid: [\n---\n")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("bad.md") != "ingested"
        assert engine.ingest_path("bad.md") != "ingested"
        error = db.connection.execute(
            "SELECT source_hash, stage, attempt_count, status FROM ingestion_errors"
        ).fetchone()
        assert error["source_hash"] == hashlib.sha256(
            (config.vault_path / "bad.md").read_bytes()
        ).hexdigest()
        assert error["stage"] == "parse"
        assert error["attempt_count"] == 2
        assert error["status"] == "unresolved"
    finally:
        db.close()


def test_reconciliation_removes_deleted_documents_and_errors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = config.vault_path / "note.md"
    path.write_text("# Indexed\ntext", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        path.unlink()
        report = engine.scan()
        assert report.removed == 1
        assert db.get_source("note.md") is None
        assert not db.get_chunks("note.md")
        assert db.fts_search("Indexed") == []
    finally:
        db.close()


def test_scan_reports_per_file_progress(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.vault_path / "a.md").write_text("# A\none", encoding="utf-8")
    (config.vault_path / "b.md").write_text("# B\ntwo", encoding="utf-8")
    db, engine = _engine(config)
    progress: list[tuple[int, int, str, object]] = []
    try:
        report = engine.scan(lambda *item: progress.append(item))
        assert report.ingested == 2
        assert [(item[0], item[1], item[2]) for item in progress] == [
            (1, 2, "a.md"),
            (2, 2, "b.md"),
        ]
    finally:
        db.close()


def test_incomplete_discovery_does_not_prune_indexed_sources(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    path = config.vault_path / "note.md"
    path.write_text("# Note\nexisting", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"

        def failing_walk(
            root: str | Path,
            onerror: Callable[[OSError], None],
            followlinks: bool,
        ) -> Iterator[tuple[str, list[str], list[str]]]:
            assert followlinks is False
            onerror(
                PermissionError(13, "permission denied", str(config.vault_path))
            )
            return iter(())

        with patch("matlock2.ingest.os.walk", failing_walk):
            report = engine.scan()
        assert not report.discovery_complete
        assert report.failures[0].stage == "discover"
        assert db.get_source("note.md") is not None
        assert db.unresolved_error_count() == 1
    finally:
        db.close()


def test_database_update_rolls_back_after_invalid_vector(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.vault_path / "note.md").write_text("# Before\nstable text", encoding="utf-8")
    db, engine = _engine(config)
    try:
        assert engine.ingest_path("note.md") == "ingested"
        original = [row["content"] for row in db.get_chunks("note.md")]
        original_vector_count = db.connection.execute(
            "SELECT count(*) FROM vec_chunks"
        ).fetchone()[0]
        with pytest.raises(DatabaseError):
            db.replace_document(
                "note.md",
                "new-hash",
                "new-fingerprint",
                {},
                "test-model",
                "test-version",
                [Chunk("replacement-id", 0, "replacement text")],
                [[1.0]],
            )
        assert [row["content"] for row in db.get_chunks("note.md")] == original
        assert db.fts_search("stable")
        assert db.fts_search("replacement") == []
        assert db.connection.execute("SELECT count(*) FROM vec_chunks").fetchone()[0] == (
            original_vector_count
        )
    finally:
        db.close()


def test_database_rejects_vector_dimension_change(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config.database_path, config)
    db.close()
    incompatible = replace(config, embedding_dimensions=3)
    with pytest.raises(DatabaseError, match="Rebuild or migrate"):
        Database(incompatible.database_path, incompatible)


def test_config_load_and_validation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config_path = tmp_path / "matlock2.toml"
    config_path.write_text(
        '[ingest]\nvault_path = "vault"\ndatabase_path = "db/index.sqlite3"\n'
        "[chunking]\ntarget_tokens = 20\noverlap_tokens = 3\n"
        "[embedding]\ndimensions = 2\nmax_input_tokens = 24\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.vault_path == vault
    assert config.database_path == tmp_path / "db" / "index.sqlite3"

    config_path.write_text(
        '[ingest]\nvault_path = "vault"\n[chunking]\ntarget_tokens = 20\n'
        "overlap_tokens = 20\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(config_path)


def test_cli_validates_sample_configuration_without_loading_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config_path = tmp_path / "matlock2.toml"
    config_path.write_text(
        '[ingest]\nvault_path = "vault"\n[hooks]\n'
        'configured = ["matlock2.hooks:project_id_from_path"]\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config_path), "check-config"]) == 0
    assert "Configuration valid" in capsys.readouterr().out


def test_watcher_event_handler_filters_and_tracks_moves(tmp_path: Path) -> None:
    import queue

    vault = tmp_path / "vault"
    vault.mkdir()
    events: queue.Queue[object] = queue.Queue()
    handler = _VaultEventHandler(vault, events)
    handler.on_any_event(FileModifiedEvent(str(vault / "note.md")))
    handler.on_any_event(
        FileMovedEvent(
            str(vault / "old.md"),
            str(vault / "new.md"),
        )
    )
    assert events.get_nowait() == _PendingEvent("upsert", "note.md")
    assert events.get_nowait() == _PendingEvent("delete", "old.md")
    assert events.get_nowait() == _PendingEvent("upsert", "new.md")
    handler.on_any_event(FileModifiedEvent(str(vault / "ignore.txt")))
    assert events.empty()


def test_watcher_ingests_created_file_and_stops_cleanly(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(config, reconcile_interval_seconds=3600)
    startup_complete = threading.Event()
    stop_event = threading.Event()
    thread_errors: list[BaseException] = []

    def run_watcher() -> None:
        db = Database(config.database_path, config)
        try:
            engine = IngestionEngine(config, db, TestEmbedder())
            VaultWatcher(
                engine,
                lambda _: startup_complete.set(),
                stop_event=stop_event,
            ).run()
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            db.close()

    thread = threading.Thread(target=run_watcher)
    thread.start()
    assert startup_complete.wait(timeout=5)
    path = config.vault_path / "observed.md"
    path.write_text("# Watcher\nfilesystem event", encoding="utf-8")
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            db = Database(config.database_path, config)
            try:
                if db.get_source("observed.md") is not None:
                    break
            finally:
                db.close()
            time.sleep(0.05)
        else:
            pytest.fail("Watcher did not ingest the created Markdown file")
    finally:
        stop_event.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not thread_errors


def test_approved_fake_corpus_can_be_ingested_optionally(tmp_path: Path) -> None:
    corpus = os.environ.get("MATLOCK2_TEST_VAULT")
    if not corpus:
        pytest.skip("Set MATLOCK2_TEST_VAULT to run the optional fake-corpus test")
    source = Path(corpus).resolve()
    if not source.is_dir():
        pytest.skip("Configured fake corpus is unavailable")
    config = IngestConfig(
        vault_path=source,
        database_path=tmp_path / "fake-corpus.sqlite3",
        target_tokens=400,
        overlap_tokens=50,
        embedding_dimensions=2,
        max_input_tokens=512,
    )
    db, engine = _engine(config)
    try:
        report = engine.scan()
        assert report.discovered > 0
        assert report.discovery_complete
    finally:
        db.close()
