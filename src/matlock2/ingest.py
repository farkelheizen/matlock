from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, cast

from .chunking import chunk_markdown
from .database import Database
from .embedding import FastEmbedder
from .markdown import parse_markdown
from .models import (
    DocumentContext,
    Embedder,
    IngestConfig,
    IngestFailure,
    ScanReport,
    Tokenizer,
)

MetadataHook = Callable[[DocumentContext, dict[str, Any]], None]
ProgressCallback = Callable[[int, int, str, str | IngestFailure], None]


@dataclass(frozen=True)
class _LoadedHook:
    name: str
    callback: MetadataHook
    source_digest: str


def _load_hooks(specifications: tuple[str, ...]) -> tuple[_LoadedHook, ...]:
    loaded: list[_LoadedHook] = []
    for specification in specifications:
        module_name, callable_name = specification.split(":", maxsplit=1)
        module: ModuleType = importlib.import_module(module_name)
        callback = getattr(module, callable_name, None)
        if not callable(callback):
            raise ValueError(f"Configured hook is not callable: {specification}")
        try:
            inspect.signature(callback).bind(DocumentContext("", ""), {})
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Hook must accept (document_context, metadata): {specification}"
            ) from exc
        source_file = inspect.getsourcefile(callback) or getattr(module, "__file__", None)
        if source_file:
            source_digest = hashlib.sha256(Path(source_file).read_bytes()).hexdigest()
        else:
            try:
                source_digest = hashlib.sha256(inspect.getsource(callback).encode()).hexdigest()
            except (OSError, TypeError):
                source_digest = "source-unavailable"
        loaded.append(_LoadedHook(specification, callback, source_digest))
    return tuple(loaded)


def _safe_error(exc: Exception, vault_root: Path, stage: str) -> str:
    if stage in {"hooks", "embed"}:
        return (
            f"{type(exc).__name__}: operation failed; details omitted to prevent "
            "document content from entering logs"
        )
    message = " ".join(str(exc).split())
    message = message.replace(str(vault_root), "<vault>")
    return f"{type(exc).__name__}: {message}"[:1000]


class IngestionEngine:
    def __init__(
        self,
        config: IngestConfig,
        database: Database,
        embedder: Embedder | None = None,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self.config = config
        self.database = database
        if embedder is None:
            embedder = FastEmbedder(
                config.embedding_model,
                config.embedding_dimensions,
                config.max_input_tokens,
            )
        self.embedder = embedder
        if tokenizer is not None:
            self.tokenizer = tokenizer
        elif callable(getattr(embedder, "count", None)):
            self.tokenizer = cast(Tokenizer, embedder)
        else:
            raise TypeError("An explicit tokenizer is required for this embedder")
        self.hooks = _load_hooks(config.hooks)
        self.processing_fingerprint = self._processing_fingerprint()

    def _processing_fingerprint(self) -> str:
        properties: dict[str, Any] = {
            "parser": "frontmatter-v1",
            "chunker": "heading-aware-v1",
            "target_tokens": self.config.target_tokens,
            "overlap_tokens": self.config.overlap_tokens,
            "max_input_tokens": self.config.max_input_tokens,
            "embedding_model": self.config.embedding_model,
            "embedding_dimensions": self.config.embedding_dimensions,
            "hooks": [(hook.name, hook.source_digest) for hook in self.hooks],
            "tokenizer_version": getattr(self.embedder, "tokenizer_version", "injected"),
            "embedding_runtime_version": getattr(
                self.embedder, "runtime_version", "injected"
            ),
        }
        serialized = json.dumps(properties, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def ingest_path(self, relative_path: str) -> str | IngestFailure:
        stage = "read"
        source_hash = ""
        try:
            candidate = (self.config.vault_path / relative_path).resolve(strict=True)
            candidate.relative_to(self.config.vault_path.resolve())
            if candidate.suffix.lower() != ".md" or not candidate.is_file():
                raise ValueError("Source must be a Markdown file inside the configured vault")
            raw_bytes = candidate.read_bytes()
            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            existing = self.database.get_source(relative_path)
            if (
                existing is not None
                and existing["sha256"] == source_hash
                and existing["processing_fingerprint"] == self.processing_fingerprint
            ):
                self.database.resolve_errors(relative_path, source_hash)
                return "skipped"

            stage = "decode"
            markdown = raw_bytes.decode("utf-8-sig")
            stage = "parse"
            parsed = parse_markdown(relative_path, markdown)
            metadata = parsed.metadata

            stage = "hooks"
            context = DocumentContext(parsed.relative_path, parsed.body)
            for hook in self.hooks:
                result = hook.callback(context, metadata)
                if result is not None:
                    raise TypeError(f"Hook must mutate metadata and return None: {hook.name}")
                json.dumps(metadata, ensure_ascii=False, allow_nan=False)

            stage = "chunk"
            chunks = chunk_markdown(
                relative_path,
                parsed.body,
                self.tokenizer,
                self.config.target_tokens,
                self.config.overlap_tokens,
                self.config.max_input_tokens,
            )
            stage = "embed"
            vectors = self.embedder.embed([chunk.content for chunk in chunks]) if chunks else []
            if len(vectors) != len(chunks):
                raise ValueError("Embedding count does not match chunk count")
            if any(len(vector) != self.config.embedding_dimensions for vector in vectors):
                raise ValueError("Embedding dimension does not match configured dimension")
            if any(not math.isfinite(value) for vector in vectors for value in vector):
                raise ValueError("Embedding contains a non-finite value")

            stage = "database"
            self.database.replace_document(
                relative_path,
                source_hash,
                self.processing_fingerprint,
                metadata,
                self.config.embedding_model,
                str(getattr(self.embedder, "version", "injected")),
                chunks,
                vectors,
            )
            return "ingested"
        except Exception as exc:
            failure = IngestFailure(
                relative_path,
                source_hash,
                stage,
                _safe_error(exc, self.config.vault_path, stage),
            )
            self.database.record_error(relative_path, source_hash, stage, failure.error)
            return failure

    def scan(self, progress: ProgressCallback | None = None) -> ScanReport:
        discovered: list[str] = []
        failures: list[IngestFailure] = []
        ingested = 0
        skipped = 0
        discovery_complete = True

        def on_error(error: OSError) -> None:
            nonlocal discovery_complete
            discovery_complete = False
            raw_path = Path(error.filename) if error.filename else self.config.vault_path
            try:
                relative_path = raw_path.relative_to(self.config.vault_path).as_posix()
            except ValueError:
                relative_path = "<vault>"
            failure = IngestFailure(
                relative_path,
                "",
                "discover",
                _safe_error(error, self.config.vault_path, "discover"),
            )
            failures.append(failure)
            self.database.record_error(
                relative_path, "", "discover", failure.error
            )

        for root, directories, filenames in os.walk(
            self.config.vault_path, onerror=on_error, followlinks=False
        ):
            directories[:] = sorted(
                name
                for name in directories
                if not (Path(root) / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = Path(root) / filename
                if path.suffix.lower() != ".md" or path.is_symlink():
                    continue
                relative_path = path.relative_to(self.config.vault_path).as_posix()
                discovered.append(relative_path)

        for index, relative_path in enumerate(discovered, start=1):
            result = self.ingest_path(relative_path)
            if result == "ingested":
                ingested += 1
            elif result == "skipped":
                skipped += 1
            else:
                failures.append(result)
            if progress is not None:
                progress(index, len(discovered), relative_path, result)

        removed = 0
        if discovery_complete:
            removed = self.database.reconcile_sources(set(discovered))
            self.database.reconcile_errors(set(discovered))
        return ScanReport(
            discovered=len(discovered),
            ingested=ingested,
            skipped=skipped,
            removed=removed,
            failures=tuple(failures),
            unresolved_errors=self.database.unresolved_error_count(),
            discovery_complete=discovery_complete,
        )

    def ingest_with_retries(self, relative_path: str) -> str | IngestFailure:
        result: str | IngestFailure = "skipped"
        for attempt in range(self.config.retry_attempts):
            result = self.ingest_path(relative_path)
            if not isinstance(result, IngestFailure):
                return result
            if attempt + 1 < self.config.retry_attempts:
                delay = min(self.config.retry_base_seconds * (2**attempt), 30.0)
                time.sleep(delay)
        return result
