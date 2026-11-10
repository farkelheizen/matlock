from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class IngestConfig:
    vault_path: Path
    database_path: Path
    busy_timeout_ms: int = 5000
    reconcile_interval_seconds: int = 300
    retry_attempts: int = 3
    retry_base_seconds: float = 1.0
    target_tokens: int = 400
    overlap_tokens: int = 50
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    max_input_tokens: int = 512
    hooks: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentContext:
    relative_path: str
    body: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    ordinal: int
    content: str


@dataclass(frozen=True)
class ParsedDocument:
    relative_path: str
    body: str
    # The frozen record still contains mutable metadata for in-place hooks.
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IngestFailure:
    relative_path: str
    source_hash: str
    stage: str
    error: str


@dataclass(frozen=True)
class ScanReport:
    discovered: int
    ingested: int
    skipped: int
    removed: int
    failures: tuple[IngestFailure, ...]
    unresolved_errors: int
    discovery_complete: bool = True


class Tokenizer(Protocol):
    def count(self, text: str) -> int: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
