from __future__ import annotations

import argparse
import sys
from pathlib import Path

_COMMANDS = {
    "audit": "Run or configure content audits",
    "check-config": "Validate the Matlock configuration",
    "ingest": "Ingest the vault once and reconcile deletions",
    "watch": "Watch the vault and periodically reconcile it",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matlock")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("matlock.toml"),
        help="TOML configuration file (default: ./matlock.toml)",
    )
    commands = parser.add_subparsers(dest="command")
    for name, description in _COMMANDS.items():
        command = commands.add_parser(name, help=description)
        if name == "audit":
            command.add_argument(
                "audit_command",
                nargs="?",
                choices=("check-config", "scan", "run", "scan-file", "reset"),
                help="Audit operation",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    config = Path("matlock.toml")

    if values and values[0] == "--config":
        if len(values) < 2:
            parser.error("--config requires a path")
        config = Path(values[1])
        del values[:2]
    elif values and values[0].startswith("--config="):
        config = Path(values.pop(0).partition("=")[2])

    if not values or values[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command, child_args = values[0], values[1:]
    if command not in _COMMANDS:
        parser.error(f"invalid choice: {command!r}")
    if "--config" in child_args or any(arg.startswith("--config=") for arg in child_args):
        parser.error("--config must appear before the command")

    if command == "audit":
        try:
            from matlock_audit.cli import main as audit_main
        except ModuleNotFoundError as exc:
            if exc.name != "pydantic":
                raise
            print(
                "Audit dependencies are missing; install 'matlock[audit]'.",
                file=sys.stderr,
            )
            return 2

        return audit_main(["--config", str(config), *child_args])

    from matlock_ingest.cli import main as ingest_main

    return ingest_main(["--config", str(config), command, *child_args])
