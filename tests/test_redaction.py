from __future__ import annotations

from contextlib import nullcontext

import matlock.redaction as redaction


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
