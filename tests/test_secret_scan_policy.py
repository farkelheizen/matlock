from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("detect_secrets")

from matlock2_mcp import SearchConfig, SearchService, SecretScanPolicy
from matlock2_ingest.config import load_config
from tests.scanner_helpers import FAKE_AWS_KEY, build_index, make_engine


def search(config_path: Path, policy: SecretScanPolicy | None) -> SearchService:
    return SearchService(SearchConfig(load_config(config_path).database_path), policy=policy)


def sources(result: dict) -> set[str]:
    return {r["source_file"] for r in result["results"]}


def test_unscanned_content_is_hidden_by_default_and_allowed_on_request(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    assert search(config, SecretScanPolicy()).full_text_search("gardening")["results"] == []
    open_policy = SecretScanPolicy(hide_unscanned=False)
    assert sources(search(config, open_policy).full_text_search("gardening")) == {"notes/safe.md"}


def test_risky_chunks_hidden_while_clean_chunks_remain(tmp_path: Path) -> None:
    config = build_index(tmp_path, {
        "n.md": f"# Notes\n## Fine\nGardening tips here.\n## Old Server Config\nkey = {FAKE_AWS_KEY}\n"
    })
    make_engine(config, auditor=None).run_pass()
    service = search(config, SecretScanPolicy(use_document_results=False))
    assert len(service.full_text_search("Gardening")["results"]) == 1
    assert service.full_text_search("Server Config")["results"] == []
    assert service.get_chunk(service.full_text_search("Gardening")["results"][0]["chunk_id"])["result"]


def test_document_result_hides_whole_document_by_default(tmp_path: Path) -> None:
    config = build_index(tmp_path, {
        "n.md": f"# Notes\n## Fine\nGardening tips here.\n## Old\nkey = {FAKE_AWS_KEY}\n",
        "ok.md": "# Ok\nGardening for everyone.\n",
    })
    make_engine(config, auditor=None).run_pass()
    results = search(config, SecretScanPolicy()).full_text_search("Gardening")
    assert sources(results) == {"ok.md"}


def test_threshold_and_validation(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"n.md": f"# N\nkey = {FAKE_AWS_KEY}\n"})
    make_engine(config, auditor=None).run_pass()
    assert search(config, SecretScanPolicy(threshold=9)).full_text_search("key")["results"]
    assert not search(config, SecretScanPolicy(threshold=8)).full_text_search("key")["results"]
    for bad in (-1, 11, True, 2.5):
        with pytest.raises(ValueError):
            SecretScanPolicy(threshold=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SecretScanPolicy(key="bad key")


@pytest.mark.parametrize(
    ("attributes", "hidden"),
    [
        (None, True),
        ({}, True),
        ({"secret_scan": "oops"}, True),
        ({"secret_scan": {"risk_score": "high"}}, True),
        ({"secret_scan": {"risk_score": True}}, True),
        ({"secret_scan": {"risk_score": 4}}, False),
        ({"secret_scan": {"risk_score": 5}}, True),
        ({"secret_scan": {"risk_score": 0, "llm_status": "skipped", "skip_reason": "oversize"}}, True),
        ({"secret_scan": {"risk_score": 0, "llm_status": "skipped", "skip_reason": "pre_scan"}}, False),
        ({"other": 1, "secret_scan": {"risk_score": 0}}, False),
    ],
)
def test_decision_matrix(attributes: dict | None, hidden: bool) -> None:
    policy = SecretScanPolicy()
    record = {"attributes": attributes}
    assert (policy.chunk(record).action == "hide") is hidden
    assert (policy.document(record).action == "hide") is hidden
    assert policy.rewrite("any", {"a": 1}) == {"a": 1}
