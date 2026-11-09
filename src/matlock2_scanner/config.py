from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from matlock2.config import ConfigurationError, load_config
from matlock2.models import IngestConfig

from .models import DEFAULT_PROMPT, DOMAINS, HOOK_SLOTS, LAYERS, ScannerConfig

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOP_KEYS = {
    "enabled_layers", "interval_seconds", "batch_size", "domains", "attribute_key",
    "max_chunk_chars", "max_document_chars", "heuristics", "llm", "hooks",
}
_HEURISTIC_KEYS = {"plugins"}
_LLM_KEYS = {"model_id", "device", "max_new_tokens", "prompt_file", "prompt"}


def _table(parent: dict[str, Any], name: str, label: str) -> dict[str, Any]:
    value = parent.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"[{label}] must be a TOML table")
    return value


def _reject_unknown(table: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigurationError(f"Unknown key(s) in [{label}]: {', '.join(unknown)}")


def _string_list(value: Any, name: str, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{name} must be an array of strings")
    if allowed is not None:
        bad = [item for item in value if item not in allowed]
        if bad:
            raise ConfigurationError(f"{name} contains unsupported values {bad}; use {list(allowed)}")
    return tuple(value)


def _positive_int(table: dict[str, Any], name: str, default: int, label: str) -> int:
    value = table.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{label}.{name} must be a positive integer")
    return value


def validate_hook_spec(spec: str, slot: str) -> None:
    if not isinstance(spec, str) or ":" not in spec or not all(spec.split(":", maxsplit=1)):
        raise ConfigurationError(
            f"Hook {slot} must be a single module:function string, got {spec!r}"
        )


def load_scanner_config(path: Path) -> tuple[IngestConfig, ScannerConfig]:
    """Load the ingest configuration plus the optional [secret_scanner] table."""
    ingest_config = load_config(path)
    config_path = path.expanduser().resolve()
    with config_path.open("rb") as stream:
        data = tomllib.load(stream)
    raw = data.get("secret_scanner", {})
    if not isinstance(raw, dict):
        raise ConfigurationError("[secret_scanner] must be a TOML table")
    _reject_unknown(raw, _TOP_KEYS, "secret_scanner")
    base = config_path.parent

    layers = _string_list(
        raw.get("enabled_layers", list(LAYERS)), "secret_scanner.enabled_layers", LAYERS
    )
    domains = _string_list(raw.get("domains", list(DOMAINS)), "secret_scanner.domains", DOMAINS)
    if not layers:
        raise ConfigurationError("secret_scanner.enabled_layers must not be empty")
    if not domains:
        raise ConfigurationError("secret_scanner.domains must not be empty")
    attribute_key = raw.get("attribute_key", "secret_scan")
    if not isinstance(attribute_key, str) or not _KEY_PATTERN.match(attribute_key):
        raise ConfigurationError("secret_scanner.attribute_key must be an ASCII identifier")

    heuristics = _table(raw, "heuristics", "secret_scanner.heuristics")
    _reject_unknown(heuristics, _HEURISTIC_KEYS, "secret_scanner.heuristics")
    plugins = _string_list(
        heuristics.get("plugins", []), "secret_scanner.heuristics.plugins"
    )

    llm = _table(raw, "llm", "secret_scanner.llm")
    _reject_unknown(llm, _LLM_KEYS, "secret_scanner.llm")
    if "prompt" in llm and "prompt_file" in llm:
        raise ConfigurationError("Set only one of llm.prompt and llm.prompt_file")
    prompt, prompt_source = DEFAULT_PROMPT, "default"
    if "prompt_file" in llm:
        prompt_path = Path(str(llm["prompt_file"])).expanduser()
        prompt_path = prompt_path if prompt_path.is_absolute() else base / prompt_path
        try:
            prompt = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(f"Cannot read llm.prompt_file: {prompt_path}") from exc
        prompt_source = "file"
    elif "prompt" in llm:
        if not isinstance(llm["prompt"], str):
            raise ConfigurationError("llm.prompt must be a string")
        prompt, prompt_source = llm["prompt"], "inline"
    if "{content}" not in prompt:
        raise ConfigurationError("The LLM prompt must contain the {content} placeholder")
    for name in ("model_id", "device"):
        if name in llm and (not isinstance(llm[name], str) or not llm[name]):
            raise ConfigurationError(f"llm.{name} must be a non-empty string")

    hooks_table = _table(raw, "hooks", "secret_scanner.hooks")
    _reject_unknown(hooks_table, set(HOOK_SLOTS), "secret_scanner.hooks")
    for slot, spec in hooks_table.items():
        validate_hook_spec(spec, slot)

    scanner = ScannerConfig(
        enabled_layers=layers,
        interval_seconds=_positive_int(raw, "interval_seconds", 60, "secret_scanner"),
        batch_size=_positive_int(raw, "batch_size", 50, "secret_scanner"),
        domains=domains,
        attribute_key=attribute_key,
        max_chunk_chars=_positive_int(raw, "max_chunk_chars", 4000, "secret_scanner"),
        max_document_chars=_positive_int(raw, "max_document_chars", 50000, "secret_scanner"),
        heuristic_plugins=plugins,
        llm_model_id=llm.get("model_id", ScannerConfig.llm_model_id),
        llm_device=llm.get("device", "auto"),
        llm_max_new_tokens=_positive_int(llm, "max_new_tokens", 256, "secret_scanner.llm"),
        prompt=prompt,
        prompt_source=prompt_source,
        hooks=dict(hooks_table),
        config_dir=base,
    )
    return ingest_config, scanner
