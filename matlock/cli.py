"""
Matlock CLI entrypoint.

Usage:
    matlock --config PATH sync [--force]
    matlock --config PATH parse
    matlock --config PATH map-projects
"""

from __future__ import annotations

from pathlib import Path

import typer

from matlock.config import load_config, validate_config_paths
from matlock.db import get_connection, init_db
from matlock.stages.map_projects import run_map_projects
from matlock.stages.parse import run_parse
from matlock.stages.sync import run_sync

app = typer.Typer(
    name="matlock",
    help="Matlock — Markdown task & project engine.",
    add_completion=False,
)

# Global state passed from the callback to subcommands via the Typer context
_CONFIG_KEY = "config"


@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = typer.Option(
        Path("config.yaml"),
        "--config",
        "-c",
        help="Path to config.yaml.",
        show_default=True,
    ),
) -> None:
    """Matlock — Markdown task & project engine."""
    ctx.ensure_object(dict)
    ctx.obj[_CONFIG_KEY] = config


@app.command()
def sync(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-mark all files as needing parsing, regardless of hash.",
    ),
) -> None:
    """Sync the file table with the vault on disk."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_sync(cfg, conn, force=force)
    finally:
        conn.close()

    typer.echo(
        f"Sync complete: {result.inserted} inserted, {result.updated} updated, "
        f"{result.unchanged} unchanged, {result.deleted} deleted"
    )


def _load_and_validate(config_path: Path):
    """Shared helper: load config and validate paths. Exits on error."""
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        typer.echo(f"Error: config file not found: {config_path}", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: invalid config: {exc}", err=True)
        raise typer.Exit(code=1)

    try:
        validate_config_paths(cfg)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    return cfg


@app.command()
def parse(ctx: typer.Context) -> None:
    """Parse all files flagged by sync (needs_parsing=1)."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_parse(cfg, conn)
    finally:
        conn.close()

    typer.echo(
        f"Parse complete: {result.parsed} parsed, {result.skipped} skipped, "
        f"{result.tasks_inserted} tasks inserted, {result.tasks_deleted} tasks deleted"
    )


@app.command(name="map-projects")
def map_projects(ctx: typer.Context) -> None:
    """Rebuild project-to-file associations from config."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_map_projects(cfg, conn)
    finally:
        conn.close()

    typer.echo(
        f"Map-projects complete: {result.super_projects_written} super-projects, "
        f"{result.projects_written} projects, "
        f"{result.file_project_rows} file-project links"
    )
