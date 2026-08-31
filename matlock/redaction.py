from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """Normalized detect-secrets finding details for one reported location."""

    line_number: int | None
    secret_value: str | None
    hashed_secret: str | None
    secret_type: str | None


@dataclass(frozen=True, slots=True)
class SecretScanResult:
    """Secret-detection status attached to a document parse outcome."""

    has_secrets: bool
    secret_detection_error: str | None
    findings: tuple[SecretFinding, ...] = ()


REDACTED_PLACEHOLDER = "[REDACTED]"


def scan_document_for_secrets(file_path: str | Path) -> SecretScanResult:
    """Scan one document with detect-secrets and return a fail-closed result."""
    try:
        collection = SecretsCollection()
        with default_settings():
            collection.scan_file(str(file_path))
        payload = collection.json()
    except Exception as exc:
        return SecretScanResult(
            has_secrets=True,
            secret_detection_error=str(exc),
            findings=(),
        )

    findings = tuple(_extract_findings(payload))
    return SecretScanResult(
        has_secrets=bool(findings),
        secret_detection_error=None,
        findings=findings,
    )


def get_redacted_document(file_path: str | Path, cache_dir: str | Path) -> str:
    """Return a redacted document body, caching successful redactions by hash."""
    source = Path(file_path)
    raw_bytes = source.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    cache_root = Path(cache_dir).expanduser()
    cache_file = cache_root / f"{file_hash}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    scan_result = scan_document_for_secrets(source)
    if scan_result.secret_detection_error is not None:
        return _full_document_placeholder(raw_bytes)

    original_text = raw_bytes.decode("utf-8")
    if not scan_result.has_secrets:
        redacted_text = original_text
    else:
        redacted_text = _redact_text(original_text, scan_result.findings)

    cache_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(cache_file, redacted_text)
    return redacted_text


def _extract_findings(payload: Any) -> list[SecretFinding]:
    if not isinstance(payload, dict):
        return []

    results = payload.get("results")
    if not isinstance(results, dict):
        return []

    findings: list[SecretFinding] = []
    for file_findings in results.values():
        if not isinstance(file_findings, list):
            continue
        for finding in file_findings:
            if not isinstance(finding, dict):
                continue
            findings.append(
                SecretFinding(
                    line_number=_to_int(finding.get("line_number")),
                    secret_value=_first_str(
                        finding,
                        "secret_value",
                        "secret",
                        "value",
                    ),
                    hashed_secret=_to_str_or_none(finding.get("hashed_secret")),
                    secret_type=_first_str(finding, "type", "secret_type"),
                )
            )
    return findings


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _to_str_or_none(mapping.get(key))
        if text is not None:
            return text
    return None


def _to_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _redact_text(text: str, findings: tuple[SecretFinding, ...]) -> str:
    lines = text.splitlines(keepends=True)
    line_map: dict[int, list[SecretFinding]] = {}
    for finding in findings:
        if finding.line_number is None or finding.line_number < 1:
            continue
        line_map.setdefault(finding.line_number, []).append(finding)

    for line_number, line_findings in line_map.items():
        index = line_number - 1
        if index >= len(lines):
            continue
        line = lines[index]
        replaced = line
        had_value_replacement = False
        for finding in line_findings:
            if finding.secret_value and finding.secret_value in replaced:
                replaced = replaced.replace(finding.secret_value, REDACTED_PLACEHOLDER)
                had_value_replacement = True
        if had_value_replacement:
            lines[index] = replaced
            continue
        line_ending = "\n" if line.endswith("\n") else ""
        lines[index] = f"{REDACTED_PLACEHOLDER}{line_ending}"

    return "".join(lines)


def _full_document_placeholder(raw_bytes: bytes) -> str:
    line_count = max(len(raw_bytes.splitlines()), 1)
    return os.linesep.join(REDACTED_PLACEHOLDER for _ in range(line_count))


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


__all__ = [
    "REDACTED_PLACEHOLDER",
    "SecretFinding",
    "SecretScanResult",
    "get_redacted_document",
    "scan_document_for_secrets",
]
