from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from matlock2_ingest.config import ConfigurationError

from .models import Finding, ScanItem, ScanResult, ScannerConfig

_ITEM = ScanItem("chunk", "0" * 64, "")
_RESULT = ScanResult(risk_score=0)
# Dummy arguments used to verify each hook's call signature at load time.
_SIGNATURES: dict[str, tuple[Any, ...]] = {
    "pre_scan": (_ITEM,),
    "heuristic_rules": (_ITEM,),
    "prompt_context": (_ITEM, ""),
    "risk_policy": (_ITEM, _RESULT),
    "on_oversize": (_ITEM, 1),
    "post_scan": (_ITEM, _RESULT),
}


@dataclass(frozen=True)
class Hooks:
    pre_scan: Callable[[ScanItem], bool] | None = None
    heuristic_rules: Callable[[ScanItem], list[Finding]] | None = None
    prompt_context: Callable[[ScanItem, str], str] | None = None
    risk_policy: Callable[[ScanItem, ScanResult], int] | None = None
    on_oversize: Callable[[ScanItem, int], str | None] | None = None
    post_scan: Callable[[ScanItem, ScanResult], None] | None = None


def load_hooks(config: ScannerConfig) -> Hooks:
    # Hook modules next to the config file (as in the examples) are importable.
    directory = str(config.config_dir)
    if directory not in sys.path:
        sys.path.append(directory)
    loaded: dict[str, Callable[..., Any]] = {}
    for slot, spec in config.hooks.items():
        module_name, callable_name = spec.split(":", maxsplit=1)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ConfigurationError(f"Cannot import hook module for {slot}: {spec}") from exc
        callback = getattr(module, callable_name, None)
        if not callable(callback):
            raise ConfigurationError(f"Configured hook {slot} is not callable: {spec}")
        try:
            inspect.signature(callback).bind(*_SIGNATURES[slot])
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"Hook {slot} has an incompatible signature: {spec}") from exc
        loaded[slot] = callback
    return Hooks(**loaded)
