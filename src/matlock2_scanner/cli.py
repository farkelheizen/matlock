from __future__ import annotations

import argparse
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from matlock2.config import ConfigurationError

from .config import load_scanner_config
from .engine import ScanEngine
from .heuristics import HeuristicScanner, detect_secrets_available, validate_plugins
from .hooks import load_hooks
from .llm import LocalLLMAuditor, llm_available
from .models import PassReport, ScannerConfig
from .store import ScanStore, ScannerDatabaseError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matlock2-scanner")
    parser.add_argument(
        "--config", type=Path, default=Path("matlock2.toml"),
        help="TOML configuration file (default: ./matlock2.toml)",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-llm", action="store_true", help="Disable the LLM layer")
    common.add_argument("--no-heuristics", action="store_true", help="Disable the heuristic layer")
    common.add_argument("--limit", type=int, help="Maximum items to scan per pass")
    common.add_argument("--dry-run", action="store_true", help="Scan and report without writing")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check-config", parents=[common], help="Validate the scanner configuration"
    )
    commands.add_parser("scan", parents=[common], help="Scan unscanned content once")
    run = commands.add_parser("run", parents=[common], help="Scan repeatedly until stopped")
    run.add_argument("--max-passes", type=int, help="Stop after this many passes")
    return parser


def format_report(report: PassReport) -> str:
    prefix = "Dry run" if report.dry_run else "Scan"
    return (
        f"{prefix} complete: scanned={report.scanned}, skipped={report.skipped}, "
        f"flagged={report.flagged}, errors={report.errors}"
    )


def _effective_layers(config: ScannerConfig, args: argparse.Namespace | None) -> tuple[str, ...]:
    layers = config.enabled_layers
    if args is not None:
        if getattr(args, "no_llm", False):
            layers = tuple(layer for layer in layers if layer != "llm")
        if getattr(args, "no_heuristics", False):
            layers = tuple(layer for layer in layers if layer != "heuristic")
    return layers


def _check_dependencies(config: ScannerConfig, layers: tuple[str, ...]) -> None:
    if "heuristic" in layers:
        if not detect_secrets_available():
            raise ConfigurationError(
                "The heuristic layer requires detect-secrets: pip install 'matlock2[scanner]'"
            )
        if config.heuristic_plugins:
            try:
                validate_plugins(config.heuristic_plugins)
            except Exception as exc:
                raise ConfigurationError(f"Invalid heuristics.plugins: {exc}") from exc
    if "llm" in layers and not llm_available():
        raise ConfigurationError(
            "The LLM layer requires transformers and torch: pip install 'matlock2[scanner-llm]'"
        )


def build_engine(store: ScanStore, config: ScannerConfig, layers: tuple[str, ...]) -> ScanEngine:
    hooks = load_hooks(config)
    return ScanEngine(
        store,
        config,
        hooks,
        heuristics=HeuristicScanner(config.heuristic_plugins) if "heuristic" in layers else None,
        auditor_factory=(
            (lambda: LocalLLMAuditor(config.llm_model_id, config.llm_device, config.llm_max_new_tokens))
            if "llm" in layers
            else None
        ),
    )


def run_loop(
    engine: ScanEngine,
    interval_seconds: float,
    stop: threading.Event,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    max_passes: int | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Scan, sleep, repeat. Returns the number of completed passes."""
    passes = 0
    while not stop.is_set():
        try:
            report = engine.run_pass(limit=limit, dry_run=dry_run, should_stop=stop.is_set)
            emit(format_report(report))
        except sqlite3.Error as exc:
            emit(f"ERROR: database operation failed: {exc}")
        passes += 1
        if max_passes is not None and passes >= max_passes:
            break
        stop.wait(interval_seconds)
    return passes


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store: ScanStore | None = None
    try:
        ingest_config, config = load_scanner_config(args.config)
        layers = _effective_layers(config, args)
        _check_dependencies(config, layers)
        load_hooks(config)
        if args.command == "check-config":
            print(f"Scanner configuration valid: {args.config.resolve()}")
            print(f"Database: {ingest_config.database_path}")
            print(f"Layers: {', '.join(layers) or 'none'}")
            return 0
        if args.limit is not None and args.limit < 1:
            raise ConfigurationError("--limit must be positive")
        if getattr(args, "max_passes", None) is not None and args.max_passes < 1:
            raise ConfigurationError("--max-passes must be positive")
        if not layers:
            raise ConfigurationError("No scanner layers are enabled")
        store = ScanStore(ingest_config.database_path, ingest_config.busy_timeout_ms)
        engine = build_engine(store, config, layers)
    except (ConfigurationError, ScannerDatabaseError, ImportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if store is not None:
            store.close()
        return 2

    try:
        if args.command == "scan":
            print(format_report(engine.run_pass(limit=args.limit, dry_run=args.dry_run)))
            return 0
        stop = threading.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            signal.signal(name, lambda *_: stop.set())
        print(f"Scanning every {config.interval_seconds}s; press Ctrl+C to stop.")
        run_loop(
            engine, config.interval_seconds, stop,
            limit=args.limit, dry_run=args.dry_run, max_passes=args.max_passes,
        )
        print("Scanner stopped.")
        return 0
    except (sqlite3.Error, RuntimeError, OSError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
