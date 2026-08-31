from __future__ import annotations

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


__all__ = [
    "SecretFinding",
    "SecretScanResult",
    "scan_document_for_secrets",
]
