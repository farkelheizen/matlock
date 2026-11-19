from __future__ import annotations

import pytest

pytest.importorskip("detect_secrets")

from matlock_audit.heuristics import HeuristicScanner, mask
from tests.audit_helpers import FAKE_AWS_KEY


def test_detects_fake_aws_key_with_masked_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    findings = HeuristicScanner().scan(f"line one\nkey = {FAKE_AWS_KEY}\n")
    aws = [f for f in findings if f.type == "AWS Access Key"]
    assert aws and aws[0].line == 2
    assert all(FAKE_AWS_KEY not in f.preview for f in findings)
    assert list(tmp_path.iterdir()) == []  # no baseline or other files


def test_clean_text_has_no_findings() -> None:
    assert HeuristicScanner().scan("# Notes\nNothing to see here.\n") == []


def test_plugin_selection() -> None:
    scanner = HeuristicScanner(("AWSKeyDetector",))
    assert [f.type for f in scanner.scan(f"x = {FAKE_AWS_KEY}")] == ["AWS Access Key"]


def test_mask() -> None:
    assert mask("abcdefgh") == "ab***" and mask("abc") == "***"


def test_inline_allowlist_comment_does_not_bypass() -> None:
    findings = HeuristicScanner().scan(f"k = {FAKE_AWS_KEY}  # pragma: allowlist secret")
    assert any(f.type == "AWS Access Key" for f in findings)
