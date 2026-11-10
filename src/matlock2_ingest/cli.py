from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigurationError, load_config
from .database import Database, DatabaseError
from .embedding import FastEmbedder
from .ingest import IngestionEngine
from .models import IngestConfig, IngestFailure, ScanReport
from .watcher import VaultWatcher


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matlock-ingest")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("matlock2.toml"),
        help="TOML configuration file (default: ./matlock2.toml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="Validate the configuration file")
    commands.add_parser(
        "ingest",
        aliases=["scan"],
        help="Ingest the vault once and reconcile deletions (scan is an alias)",
    )
    commands.add_parser("watch", help="Watch the vault and periodically reconcile it")
    return parser


def _print_report(report: ScanReport) -> None:
    print(
        f"Ingest complete: discovered={report.discovered}, ingested={report.ingested}, "
        f"skipped={report.skipped}, removed={report.removed}, "
        f"unresolved_errors={report.unresolved_errors}"
    )
    for failure in report.failures:
        print(f"ERROR {failure.relative_path} [{failure.stage}]: {failure.error}")
    if not report.discovery_complete:
        print("ERROR Vault discovery was incomplete; stale records were not removed.")


def _print_progress(
    current: int, total: int, relative_path: str, result: str | IngestFailure
) -> None:
    if isinstance(result, IngestFailure):
        state = f"ERROR [{result.stage}]"
    else:
        state = result
    position = f"{current}/{total} " if total else ""
    print(f"{position}{relative_path}: {state}")


def _make_engine(config: IngestConfig) -> tuple[Database, IngestionEngine]:
    # Kept local so validation of a config file does not download the embedding model.
    database = Database(config.database_path, config)
    try:
        embedder = FastEmbedder(
            config.embedding_model,
            config.embedding_dimensions,
            config.max_input_tokens,
        )
        engine = IngestionEngine(config, database, embedder)
        return database, engine
    except Exception:
        database.close()
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "check-config":
            print(f"Configuration valid: {args.config.resolve()}")
            print(f"Vault: {config.vault_path}")
            print(f"Database: {config.database_path}")
            return 0
        database, engine = _make_engine(config)
    except (
        ConfigurationError,
        DatabaseError,
        RuntimeError,
        ValueError,
        OSError,
        ImportError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command in {"ingest", "scan"}:
            report = engine.scan(_print_progress)
            _print_report(report)
            return 1 if report.unresolved_errors or not report.discovery_complete else 0
        if args.command == "watch":
            print(f"Watching {config.vault_path}")
            VaultWatcher(engine, _print_report, _print_progress).run()
            return 0
    except KeyboardInterrupt:
        if args.command == "watch":
            print("Watcher stopped.")
            return 0
        raise
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        database.close()
    return 0
