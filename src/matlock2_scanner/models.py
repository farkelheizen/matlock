from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PROMPT = """You are a strict enterprise security auditor. Analyze the following document chunk for exposed API keys, passwords, database credentials, internal tokens, or sensitive personal data.

Respond ONLY with a valid JSON object matching this schema:
{
  "risk_score": <int 0-10>,
  "regex_matches_found": <bool>,
  "llm_confidence_score": <int 0-10>,
  "reason": "<string explanation>",
  "flagged_snippets": ["<string>", ...]
}

Document Chunk:
---
{content}
---
"""

LAYERS = ("heuristic", "llm")
DOMAINS = ("chunk", "document")
HOOK_SLOTS = (
    "pre_scan",
    "heuristic_rules",
    "prompt_context",
    "risk_policy",
    "on_oversize",
    "post_scan",
)


@dataclass(frozen=True)
class ScannerConfig:
    enabled_layers: tuple[str, ...] = LAYERS
    interval_seconds: int = 60
    batch_size: int = 50
    domains: tuple[str, ...] = DOMAINS
    attribute_key: str = "secret_scan"
    max_chunk_chars: int = 4000
    max_document_chars: int = 50000
    heuristic_plugins: tuple[str, ...] = ()
    llm_model_id: str = "Qwen/Qwen2.5-1.5B-Instruct"
    llm_device: str = "auto"
    llm_max_new_tokens: int = 256
    prompt: str = DEFAULT_PROMPT
    prompt_source: str = "default"
    hooks: dict[str, str] = field(default_factory=dict)
    config_dir: Path = Path(".")


@dataclass(frozen=True)
class ScanItem:
    """One unit of content addressed by (domain, sha256)."""

    domain: str
    sha256: str
    content: str
    source_paths: tuple[str, ...] = ()
    chunk_ordinals: tuple[int, ...] = ()
    # Ingest metadata for each referencing path, keyed by relative path.
    document_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    type: str
    line: int = 0
    preview: str = ""
    source: str = "rule"


@dataclass(frozen=True)
class ScanResult:
    risk_score: int
    regex_matches_found: bool = False
    llm_confidence_score: int = 0
    reason: str = ""
    flagged_snippets: tuple[str, ...] = ()
    heuristic_findings: tuple[Finding, ...] = ()
    layers: tuple[str, ...] = ()
    llm_status: str = "disabled"
    skip_reason: str | None = None
    error: str | None = None


@dataclass
class PassReport:
    scanned: int = 0
    skipped: int = 0
    flagged: int = 0
    errors: int = 0
    dry_run: bool = False
