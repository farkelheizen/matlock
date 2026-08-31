from __future__ import annotations

import hashlib
from contextlib import nullcontext
from pathlib import Path

import matlock.redaction as redaction
from matlock.redaction import REDACTED_PLACEHOLDER, SecretFinding, SecretScanResult


class _FakeSecretsCollection:
    def __init__(self, payload: dict | None = None, scan_error: Exception | None = None):
        self._payload = payload or {"results": {}}
        self._scan_error = scan_error

    def scan_file(self, _path: str) -> None:
        if self._scan_error is not None:
            raise self._scan_error

    def json(self) -> dict:
        return self._payload


def test_scan_document_for_secrets_clean_file(monkeypatch):
    monkeypatch.setattr(redaction, "SecretsCollection", lambda: _FakeSecretsCollection())
    monkeypatch.setattr(redaction, "default_settings", lambda: nullcontext())

    result = redaction.scan_document_for_secrets("note.md")

    assert result.has_secrets is False
    assert result.secret_detection_error is None
    assert result.findings == ()


def test_scan_document_for_secrets_detected_findings(monkeypatch):
    payload = {
        "results": {
            "note.md": [
                {
                    "line_number": 4,
                    "hashed_secret": "abc123",
                    "type": "Base64 High Entropy String",
                }
            ]
        }
    }
    monkeypatch.setattr(
        redaction,
        "SecretsCollection",
        lambda: _FakeSecretsCollection(payload=payload),
    )
    monkeypatch.setattr(redaction, "default_settings", lambda: nullcontext())

    result = redaction.scan_document_for_secrets("note.md")

    assert result.has_secrets is True
    assert result.secret_detection_error is None
    assert len(result.findings) == 1
    assert result.findings[0].line_number == 4
    assert result.findings[0].hashed_secret == "abc123"
    assert result.findings[0].secret_type == "Base64 High Entropy String"


def test_scan_document_for_secrets_fail_closed_on_exception(monkeypatch):
    scan_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(
        redaction,
        "SecretsCollection",
        lambda: _FakeSecretsCollection(scan_error=scan_error),
    )
    monkeypatch.setattr(redaction, "default_settings", lambda: nullcontext())

    result = redaction.scan_document_for_secrets("note.md")

    assert result.has_secrets is True
    assert "invalid start byte" in (result.secret_detection_error or "")
    assert result.findings == ()


def test_get_redacted_document_masks_exact_secret_and_caches(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\nkeep=this\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: SecretScanResult(
            has_secrets=True,
            secret_detection_error=None,
            findings=(
                SecretFinding(
                    line_number=1,
                    secret_value="abc123",
                    hashed_secret=None,
                    secret_type="test",
                ),
            ),
        ),
    )

    output = redaction.get_redacted_document(source, cache_dir)

    assert output == f"token={REDACTED_PLACEHOLDER}\nkeep=this\n"
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_cache_file = cache_dir / file_hash[:2] / file_hash[2:4] / f"{file_hash}.txt"
    assert expected_cache_file.exists()


def test_get_redacted_document_redacts_full_line_without_secret_value(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\nkeep=this\n", encoding="utf-8")

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: SecretScanResult(
            has_secrets=True,
            secret_detection_error=None,
            findings=(
                SecretFinding(
                    line_number=1,
                    secret_value=None,
                    hashed_secret="hash",
                    secret_type="test",
                ),
            ),
        ),
    )

    output = redaction.get_redacted_document(source, tmp_path / "cache")

    assert output == f"{REDACTED_PLACEHOLDER}\nkeep=this\n"


def test_get_redacted_document_uses_cache_before_rescanning(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: SecretScanResult(
            has_secrets=True,
            secret_detection_error=None,
            findings=(
                SecretFinding(
                    line_number=1,
                    secret_value="abc123",
                    hashed_secret=None,
                    secret_type="test",
                ),
            ),
        ),
    )
    first = redaction.get_redacted_document(source, cache_dir)

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: (_ for _ in ()).throw(RuntimeError("should not rescan")),
    )
    second = redaction.get_redacted_document(source, cache_dir)

    assert first == second


def test_get_redacted_document_prefers_sharded_cache_over_legacy(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    sharded_cache_file = cache_dir / file_hash[:2] / file_hash[2:4] / f"{file_hash}.txt"
    sharded_cache_file.parent.mkdir(parents=True, exist_ok=True)
    sharded_cache_file.write_text("from-sharded", encoding="utf-8")

    legacy_cache_file = cache_dir / f"{file_hash}.txt"
    legacy_cache_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_cache_file.write_text("from-legacy", encoding="utf-8")

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: (_ for _ in ()).throw(RuntimeError("should not rescan")),
    )

    output = redaction.get_redacted_document(source, cache_dir)

    assert output == "from-sharded"


def test_get_redacted_document_reads_legacy_flat_cache_when_sharded_missing(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    legacy_cache_file = cache_dir / f"{file_hash}.txt"
    legacy_cache_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_cache_file.write_text("from-legacy", encoding="utf-8")

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: (_ for _ in ()).throw(RuntimeError("should not rescan")),
    )

    output = redaction.get_redacted_document(source, cache_dir)

    assert output == "from-legacy"


def test_get_redacted_document_does_not_cache_scan_failures(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("token=abc123\nkeep=this\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    monkeypatch.setattr(
        redaction,
        "scan_document_for_secrets",
        lambda _path: SecretScanResult(
            has_secrets=True,
            secret_detection_error="scan failed",
            findings=(),
        ),
    )

    output = redaction.get_redacted_document(source, cache_dir)

    assert output == f"{REDACTED_PLACEHOLDER}\n{REDACTED_PLACEHOLDER}"
    assert not cache_dir.exists()
