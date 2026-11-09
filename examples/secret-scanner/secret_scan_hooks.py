"""Example hooks covering the ingest metadata hook and every scanner hook."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from matlock2.models import DocumentContext
from matlock2_scanner.models import Finding, ScanItem, ScanResult

REPORT_PATH = Path(".matlock2/secret-scan-report.jsonl")
_HOSTNAME = re.compile(r"\b[a-z0-9-]+\.corp\.example\b")


def sensitivity_from_path(context: DocumentContext, metadata: dict[str, Any]) -> None:
    """Ingest hook: label documents by top-level folder unless frontmatter says otherwise."""
    folder = context.relative_path.split("/", maxsplit=1)[0]
    level = {"public": "public", "restricted": "restricted"}.get(folder, "internal")
    metadata.setdefault("sensitivity", level)


def _levels(item: ScanItem) -> set[str]:
    return {meta.get("sensitivity", "internal") for meta in item.document_metadata.values()}


def skip_public(item: ScanItem) -> bool:
    """pre_scan: content that only appears in public documents is not scanned."""
    return _levels(item) != {"public"}


def internal_hostnames(item: ScanItem) -> list[Finding]:
    """heuristic_rules: flag internal hostnames, which detect-secrets does not know about."""
    findings = []
    for number, line in enumerate(item.content.splitlines(), start=1):
        for match in _HOSTNAME.finditer(line):
            findings.append(Finding("Internal Hostname", number, match.group(0), "example-rule"))
    return findings


def add_sensitivity_context(item: ScanItem, template: str) -> str:
    """prompt_context: tell the model how sensitive the surrounding documents are."""
    levels = ", ".join(sorted(_levels(item))) or "unknown"
    return f"Context: this text comes from documents labelled {levels}.\n\n{template}"


def weight_by_sensitivity(item: ScanItem, result: ScanResult) -> int:
    """risk_policy: findings in restricted documents count for more."""
    if "restricted" in _levels(item) and result.risk_score >= 3:
        return min(10, result.risk_score + 2)
    return result.risk_score


def head_and_tail(item: ScanItem, limit: int) -> str | None:
    """on_oversize: scan the start and end of long content instead of skipping it."""
    half = limit // 2
    return f"{item.content[:half]}\n[...]\n{item.content[-half:]}"


def write_report(item: ScanItem, result: ScanResult) -> None:
    """post_scan: append a content-free summary line for risky items."""
    if result.risk_score < 5:
        return
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "domain": item.domain,
        "sha256": item.sha256,
        "risk_score": result.risk_score,
        "paths": list(item.source_paths),
        "finding_types": sorted({f.type for f in result.heuristic_findings}),
    }
    with REPORT_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
