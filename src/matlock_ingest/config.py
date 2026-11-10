from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .models import IngestConfig


class ConfigurationError(ValueError):
    """Raised when the TOML configuration is invalid."""


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_config(path: Path) -> IngestConfig:
    config_path = path.expanduser().resolve()
    try:
        with config_path.open("rb") as stream:
            data: dict[str, Any] = tomllib.load(stream)
    except OSError as exc:
        raise ConfigurationError(f"Cannot read configuration: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML configuration: {exc}") from exc

    legacy_sections = {"chunking", "embedding", "hooks", "secret_scanner"} & data.keys()
    if legacy_sections:
        legacy = ", ".join(f"[{name}]" for name in sorted(legacy_sections))
        raise ConfigurationError(
            f"Obsolete top-level section(s) {legacy}; move settings under "
            "[ingest] or rename [secret_scanner] to [audit]"
        )
    unknown_sections = data.keys() - {"ingest", "audit"}
    if unknown_sections:
        unknown = ", ".join(f"[{name}]" for name in sorted(unknown_sections))
        raise ConfigurationError(f"Unknown top-level section(s): {unknown}")

    ingest = data.get("ingest", {})
    if not isinstance(ingest, dict):
        raise ConfigurationError("[ingest] must be a TOML table")
    chunking = ingest.get("chunking", {})
    embedding = ingest.get("embedding", {})
    hooks_table = ingest.get("hooks", {})
    if not isinstance(hooks_table, dict):
        raise ConfigurationError("[ingest.hooks] must be a TOML table")
    hooks = hooks_table.get("configured", [])
    if not isinstance(chunking, dict):
        raise ConfigurationError("[ingest.chunking] must be a TOML table")
    if not isinstance(embedding, dict):
        raise ConfigurationError("[ingest.embedding] must be a TOML table")
    if not isinstance(hooks, list) or not all(isinstance(item, str) for item in hooks):
        raise ConfigurationError(
            "ingest.hooks.configured must be an array of module:function strings"
        )

    # Relative vault and database paths follow the config file, not the shell cwd.
    base = config_path.parent
    try:
        result = IngestConfig(
            vault_path=_resolve_path(ingest.get("vault_path", "."), base),
            database_path=_resolve_path(ingest.get("database_path", ".matlock/index.sqlite3"), base),
            busy_timeout_ms=int(ingest.get("busy_timeout_ms", 5000)),
            reconcile_interval_seconds=int(
                ingest.get("reconcile_interval_seconds", 300)
            ),
            retry_attempts=int(ingest.get("retry_attempts", 3)),
            retry_base_seconds=float(ingest.get("retry_base_seconds", 1.0)),
            target_tokens=int(chunking.get("target_tokens", 400)),
            overlap_tokens=int(chunking.get("overlap_tokens", 50)),
            embedding_model=str(
                embedding.get("model", "BAAI/bge-small-en-v1.5")
            ),
            embedding_dimensions=int(embedding.get("dimensions", 384)),
            max_input_tokens=int(embedding.get("max_input_tokens", 512)),
            hooks=tuple(hooks),
            config_dir=base,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid configuration value: {exc}") from exc

    validate_config(result)
    return result


def validate_config(config: IngestConfig) -> None:
    if not config.vault_path.is_dir():
        raise ConfigurationError(f"Vault path is not a directory: {config.vault_path}")
    if config.busy_timeout_ms < 1:
        raise ConfigurationError("busy_timeout_ms must be positive")
    if config.reconcile_interval_seconds < 1:
        raise ConfigurationError("reconcile_interval_seconds must be positive")
    if config.retry_attempts < 1:
        raise ConfigurationError("retry_attempts must be at least 1")
    if config.retry_base_seconds < 0:
        raise ConfigurationError("retry_base_seconds cannot be negative")
    if config.target_tokens < 1:
        raise ConfigurationError("target_tokens must be positive")
    if config.overlap_tokens < 0 or config.overlap_tokens >= config.target_tokens:
        raise ConfigurationError("overlap_tokens must be nonnegative and less than target_tokens")
    if config.embedding_dimensions < 1:
        raise ConfigurationError("embedding dimensions must be positive")
    if config.max_input_tokens < 2:
        raise ConfigurationError("max_input_tokens must be at least 2")
    if config.target_tokens > config.max_input_tokens:
        raise ConfigurationError("target_tokens cannot exceed max_input_tokens")
    for hook in config.hooks:
        if ":" not in hook or not all(hook.split(":", maxsplit=1)):
            raise ConfigurationError(f"Hook must use module:function syntax: {hook!r}")
