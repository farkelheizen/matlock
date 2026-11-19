from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("detect_secrets")

from matlock_ingest.config import load_config
from matlock_ingest.database import Database
from matlock_ingest.ingest import IngestionEngine
from matlock_audit.cli import main
from tests.audit_helpers import FakeAuditor, make_engine
from tests.test_ingest import TestEmbedder

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "secret-scanner"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns(".matlock", "__pycache__"))
    config_path = target / "matlock.toml"
    config_path.write_text(
        config_path.read_text().replace("dimensions = 384", "dimensions = 2"), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(target))
    monkeypatch.chdir(target)
    ingest = load_config(config_path)
    with Database(ingest.database_path, ingest) as database:
        report = IngestionEngine(ingest, database, TestEmbedder()).scan()
    assert report.ingested == 6 and report.unresolved_errors == 0
    return config_path


def rows(config_path: Path) -> list[dict]:
    connection = sqlite3.connect(load_config(config_path).database_path)
    connection.row_factory = sqlite3.Row
    try:
        found = connection.execute(
            "SELECT a.domain, a.attributes_json, "
            "COALESCE(c.content, d.body) AS text, "
            "COALESCE(c.source_path, d.path) AS path "
            "FROM content_attributes a "
            "LEFT JOIN chunks c ON a.domain='chunk' AND c.content_sha256=a.sha256 "
            "LEFT JOIN source_documents d ON a.domain='document' AND d.body_sha256=a.sha256"
        ).fetchall()
    finally:
        connection.close()
    return [
        {"domain": r["domain"], "scan": json.loads(r["attributes_json"])["secret_scan"],
         "text": r["text"], "path": r["path"]}
        for r in found
    ]


def test_ingest_hook_labels_documents(example: Path) -> None:
    connection = sqlite3.connect(load_config(example).database_path)
    labels = dict(connection.execute(
        "SELECT path, json_extract(metadata_json, '$.sensitivity') FROM source_documents"
    ))
    connection.close()
    assert labels["public/announcement.md"] == "public"
    assert labels["restricted/rotation-plan.md"] == "restricted"
    assert labels["notes/runbook.md"] == "internal"


def test_every_hook_and_custom_prompt_are_exercised(example: Path) -> None:
    auditor = FakeAuditor()
    report = make_engine(example, auditor=auditor).run_pass()
    assert report.errors == 0
    found = rows(example)

    # pre_scan: public-only content is skipped
    public = [r for r in found if r["path"] == "public/announcement.md"]
    assert public and all(r["scan"]["skip_reason"] == "pre_scan" for r in public)

    # chunk-level finding while sibling chunks stay clean
    server = [r for r in found if r["domain"] == "chunk" and r["path"] == "notes/old-server-config.md"]
    risky = [r for r in server if r["scan"]["regex_matches_found"]]
    clean = [r for r in server if not r["scan"]["regex_matches_found"]]
    assert len(risky) == 1 and "Old Server Config" in risky[0]["text"]
    assert risky[0]["scan"]["risk_score"] >= 8
    assert clean and all(r["scan"]["risk_score"] <= 1 for r in clean)

    # heuristic_rules: internal hostnames
    runbook = next(r for r in found if r["domain"] == "chunk" and r["path"] == "notes/runbook.md"
                   and "worker3" in r["text"])
    assert {f["type"] for f in runbook["scan"]["heuristic_findings"]} == {"Internal Hostname"}

    # risk_policy: restricted documents are weighted up (8 + 2)
    restricted = next(r for r in found if r["domain"] == "chunk" and r["path"].startswith("restricted/")
                      and r["scan"]["regex_matches_found"])
    assert restricted["scan"]["risk_score"] == 10

    # on_oversize: long content is scanned via head and tail instead of skipped
    long_doc = next(r for r in found if r["domain"] == "document" and r["path"] == "notes/long-note.md")
    assert long_doc["scan"]["llm_status"] == "ok" and long_doc["scan"]["regex_matches_found"]
    assert any("[...]" in prompt for prompt in auditor.prompts)

    # prompt file and prompt_context
    assert all("security reviewer" in prompt for prompt in auditor.prompts)
    assert any("labelled restricted" in prompt for prompt in auditor.prompts)
    assert all("{content}" not in prompt for prompt in auditor.prompts)

    # post_scan: content-free report of risky items
    lines = [json.loads(line) for line in Path(".matlock/secret-scan-report.jsonl").read_text().splitlines()]
    assert lines and all(entry["risk_score"] >= 5 for entry in lines)
    assert FAKE_AWS_KEY not in json.dumps(lines)

    # masking: no plaintext secrets stored
    stored = json.dumps([r["scan"] for r in found])
    assert FAKE_AWS_KEY not in stored and "hunter2hunter2" not in stored


def test_example_runs_in_heuristic_only_mode_via_cli(
    example: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(example), "check-config", "--no-llm"]) == 0
    assert main(["--config", str(example), "scan", "--no-llm"]) == 0
    assert "errors=0" in capsys.readouterr().out
    assert all(r["scan"]["layers"] in (["heuristic"], []) for r in rows(example))


def test_example_hook_functions_directly(example: Path) -> None:
    import secret_scan_hooks as hooks
    from matlock_audit.models import ScanItem, ScanResult

    item = ScanItem("chunk", "0" * 64, "a" * 3000, ("x.md",), (0,), {"x.md": {}})
    assert hooks.skip_public(item) is True
    assert hooks.head_and_tail(item, 10).count("a") == 10
    assert hooks.weight_by_sensitivity(item, ScanResult(risk_score=9)) == 9
