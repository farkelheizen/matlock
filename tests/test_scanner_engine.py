from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("detect_secrets")

from matlock2_ingest.config import load_config
from matlock2_scanner.hooks import Hooks
from tests.scanner_helpers import FAKE_AWS_KEY, VAULT, FakeAuditor, attributes, build_index, make_engine


def scan_of(attrs: dict, domain: str, key: str = "secret_scan") -> dict:
    return next(value[key] for (d, _), value in attrs.items() if d == domain)


def test_scans_chunks_and_documents_once(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    engine = make_engine(config, auditor=FakeAuditor())
    report = engine.run_pass()
    attrs = attributes(config)
    assert report.scanned == len(attrs) == 4  # two chunks + two document bodies, deduplicated
    assert {domain for domain, _ in attrs} == {"chunk", "document"}
    assert engine.run_pass().scanned == 0


def test_risky_chunk_is_flagged_and_siblings_stay_clean(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    make_engine(config, auditor=FakeAuditor()).run_pass()
    attrs = attributes(config)
    risky = [v["secret_scan"] for (d, _), v in attrs.items() if d == "chunk"
             if v["secret_scan"]["regex_matches_found"]]
    clean = [v["secret_scan"] for (d, _), v in attrs.items() if d == "chunk"
             if not v["secret_scan"]["regex_matches_found"]]
    assert len(risky) == 1 and risky[0]["risk_score"] >= 8
    assert clean and all(v["risk_score"] <= 1 for v in clean)
    assert FAKE_AWS_KEY not in str(attrs)  # secrets are masked in storage
    assert risky[0]["layers"] == ["heuristic", "llm"] and risky[0]["llm_status"] == "ok"
    assert risky[0]["provenance"]["prompt_sha256"]


def test_document_result_is_stored_against_body_hash(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    make_engine(config, auditor=None).run_pass()
    connection = sqlite3.connect(load_config(config).database_path)
    shas = {r[0] for r in connection.execute("SELECT body_sha256 FROM source_documents")}
    connection.close()
    assert {s for (d, s) in attributes(config) if d == "document"} == shas


def test_existing_row_blocks_scanning_and_is_never_modified(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    connection = sqlite3.connect(load_config(config).database_path)
    sha = connection.execute("SELECT content_sha256 FROM chunks LIMIT 1").fetchone()[0]
    connection.execute(
        "INSERT INTO content_attributes VALUES ('chunk', ?, '{}', '2026-01-01T00:00:00Z')", (sha,)
    )
    connection.commit()
    connection.close()
    make_engine(config, auditor=None).run_pass()
    assert attributes(config)[("chunk", sha)] == {}


def test_changed_prompt_or_hooks_do_not_trigger_rescan(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    make_engine(config, auditor=FakeAuditor()).run_pass()
    other = make_engine(config, auditor=FakeAuditor("{}"))
    other.config = other.config.__class__(**{**other.config.__dict__, "prompt": "x {content}"})
    assert other.run_pass().scanned == 0


def test_content_change_creates_new_hash_that_is_scanned(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    make_engine(config, auditor=None).run_pass()
    before = len(attributes(config))
    (tmp_path / "vault" / "notes" / "safe.md").write_text("# Safe\nCompletely new text.\n")
    from matlock2_ingest.database import Database
    from matlock2_ingest.ingest import IngestionEngine
    from tests.test_ingest import TestEmbedder

    ingest = load_config(config)
    with Database(ingest.database_path, ingest) as database:
        IngestionEngine(ingest, database, TestEmbedder()).scan()
    old = dict(attributes(config))
    report = make_engine(config, auditor=None).run_pass()
    assert report.scanned == 2  # new chunk + new document body
    assert len(attributes(config)) == before + 2
    assert all(attributes(config)[k] == v for k, v in old.items())  # ingest keeps old rows


def test_oversize_is_skipped_without_hook_and_row_is_written(tmp_path: Path) -> None:
    config = build_index(
        tmp_path, {"a.md": "# A\n" + "word " * 50 + "\n"}, "[secret_scanner]\nmax_chunk_chars = 20\n"
    )
    engine = make_engine(config, auditor=FakeAuditor())
    engine.run_pass()
    result = scan_of(attributes(config), "chunk")
    assert result["llm_status"] == "skipped" and result["skip_reason"] == "oversize"
    assert result["risk_score"] == 0
    assert engine.run_pass().scanned == 0


def test_oversize_hook_supplies_text(tmp_path: Path) -> None:
    config = build_index(
        tmp_path,
        {"a.md": f"# A\n{'filler ' * 20}\nkey = {FAKE_AWS_KEY}\n"},
        "[secret_scanner]\nmax_chunk_chars = 20\nmax_document_chars = 20\n",
    )
    auditor = FakeAuditor()
    hooks = Hooks(on_oversize=lambda item, limit: item.content[-60:])
    make_engine(config, auditor=auditor, hooks=hooks).run_pass()
    assert any(v["secret_scan"]["regex_matches_found"] for v in attributes(config).values())
    assert all(len(p) < 3000 for p in auditor.prompts)


def test_dry_run_and_limit(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    engine = make_engine(config, auditor=None)
    assert engine.run_pass(dry_run=True).scanned == 4
    assert attributes(config) == {}
    assert engine.run_pass(limit=1).scanned == 1
    assert len(attributes(config)) == 1


def test_stop_request_ends_pass_early(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    assert make_engine(config, auditor=None).run_pass(should_stop=lambda: True).scanned == 0


def test_llm_failures_are_recorded_without_aborting(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nplain\n"})

    def boom(_: str) -> str:
        raise RuntimeError("model exploded")

    make_engine(config, auditor=FakeAuditor(boom)).run_pass()
    value = scan_of(attributes(config), "chunk")
    assert value["llm_status"] == "error" and value["risk_score"] == 5
    assert "exploded" not in str(value)


def test_unparseable_output_uses_fallback(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    make_engine(config, auditor=FakeAuditor("not json")).run_pass()
    values = [v["secret_scan"] for v in attributes(config).values()]
    assert {v["llm_status"] for v in values} == {"parse_error"}
    assert {v["risk_score"] for v in values if v["regex_matches_found"]} == {8}
    assert {v["risk_score"] for v in values if not v["regex_matches_found"]} == {0}


def test_regex_flag_comes_from_layer_one_not_model(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nplain\n"})
    reply = '{"risk_score": 0, "regex_matches_found": true}'
    make_engine(config, auditor=FakeAuditor(reply)).run_pass()
    assert all(not v["secret_scan"]["regex_matches_found"] for v in attributes(config).values())


def test_custom_attribute_key(tmp_path: Path) -> None:
    config = build_index(tmp_path, scanner_toml='[secret_scanner]\nattribute_key = "audit"\n')
    make_engine(config, auditor=None).run_pass()
    assert all("audit" in v for v in attributes(config).values())


def test_requires_ingested_database(tmp_path: Path) -> None:
    from matlock2_scanner.store import ScanStore, ScannerDatabaseError

    with pytest.raises(ScannerDatabaseError):
        ScanStore(tmp_path / "missing.sqlite3", 1000)
    legacy = tmp_path / "legacy.sqlite3"
    sqlite3.connect(legacy).close()
    with pytest.raises(ScannerDatabaseError):
        ScanStore(legacy, 1000)


def test_concurrent_writer_row_wins(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nplain\n"})
    engine = make_engine(config, auditor=None)
    item = engine.store.unscanned("chunk", None)[0]
    other = sqlite3.connect(load_config(config).database_path)
    other.execute(
        "INSERT INTO content_attributes VALUES ('chunk', ?, '{\"x\": 1}', '2026-01-01T00:00:00Z')",
        (item.sha256,),
    )
    other.commit()
    other.close()
    assert engine.store.insert_results([("chunk", item.sha256, {"a": 1})], "secret_scan") == 0
    assert attributes(config)[("chunk", item.sha256)] == {"x": 1}


def test_vault_constant_matches_expectations() -> None:
    assert len(VAULT) == 2
