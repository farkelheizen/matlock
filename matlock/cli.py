"""
Matlock CLI entrypoint.

Usage:
    matlock --config PATH sync [--force]
    matlock --config PATH parse
    matlock --config PATH map-projects
    matlock --config PATH rollup [--date YYYY-MM-DD]
    matlock --config PATH report [--target {all,dashboard,projects,history}] [--project-id ID]
    matlock --config PATH run-all [--scan-projects] [--skip-rollup] [--force-sync] [--index-search]
    matlock --config PATH server [--debounce SECONDS] [--index-search] [--index-search-continuous]
    matlock --config PATH search index [--force] [--batch-size INT] [--model STRING]
    matlock --config PATH search query [QUERY] [--search-mode MODE] [--granularity LEVEL] [--limit INT] [--stdio]
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import typer

from matlock.config import load_config, validate_config_paths
from matlock.db import get_connection, get_file, init_db, set_file_secret_detection
from matlock.logging_setup import setup_logging
from matlock.redaction import get_redacted_document, scan_document_for_secrets
from matlock.search.logging import isolated_search_logging
from matlock.search.query_cli import (
    EXIT_CODE_EMBEDDING_ERROR,
    EXIT_CODE_INPUT_ERROR,
    EXIT_CODE_STORAGE_ERROR,
    build_error_response,
    format_human_response,
    load_request_json,
    run_search_request,
)
from matlock.server import run_server
from matlock.stages.map_projects import run_map_projects
from matlock.stages.parse import run_parse
from matlock.stages.report import run_report
from matlock.stages.rollup import run_rollup
from matlock.stages.search_index import run_search_index_stage
from matlock.stages.sync import run_sync

app = typer.Typer(
    name="matlock",
    help="Matlock — Markdown task & project engine.",
    add_completion=False,
)
search_app = typer.Typer(help="Search indexing and query commands.")
app.add_typer(search_app, name="search")

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

    setup_logging(cfg)
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


def _normalize_doc_read_path(base_directory: Path, file_path: str) -> tuple[Path, str]:
    """Resolve one CLI path argument to an in-vault absolute path and DB key."""
    raw_path = Path(file_path).expanduser()
    base_resolved = base_directory.resolve()
    if raw_path.is_absolute():
        abs_path = raw_path.resolve()
    else:
        abs_path = (base_resolved / raw_path).resolve()

    try:
        relative = abs_path.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError("path must be inside base_directory") from exc

    return abs_path, relative.as_posix()


@app.command(name="doc-read")
def doc_read(
    ctx: typer.Context,
    file_path: str = typer.Argument(..., help="Vault-relative or vault-contained absolute file path."),
) -> None:
    """Read a tracked document, redacting content when secret state requires it."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    try:
        abs_path, db_file_path = _normalize_doc_read_path(cfg.base_directory, file_path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        file_row = get_file(conn, db_file_path)
        if file_row is None:
            typer.echo(
                "Error: file is not tracked in the database; run sync and parse first",
                err=True,
            )
            raise typer.Exit(code=1)

        if file_row["deleted"] == 1:
            typer.echo("Error: file is marked deleted in the database", err=True)
            raise typer.Exit(code=1)

        has_secrets = file_row["has_secrets"]
        if has_secrets is None:
            scan_result = scan_document_for_secrets(abs_path)
            set_file_secret_detection(
                conn,
                db_file_path,
                has_secrets=scan_result.has_secrets,
                secret_detection_error=scan_result.secret_detection_error,
            )
            conn.commit()
            has_secrets = 1 if scan_result.has_secrets else 0

        if has_secrets:
            content = get_redacted_document(abs_path, cfg.cache.redacted_dir)
        else:
            content = abs_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        typer.echo("Error: file does not exist on disk", err=True)
        raise typer.Exit(code=1)
    except OSError as exc:
        typer.echo(f"Error: failed to read file: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        conn.close()

    typer.echo(content, nl=False)


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


@app.command()
def rollup(
    ctx: typer.Context,
    date: str = typer.Option(
        None,
        "--date",
        help="Date to roll up (YYYY-MM-DD). Defaults to yesterday.",
    ),
) -> None:
    """Calculate daily metrics for a given date (default: yesterday)."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    if date is None:
        rollup_date = datetime.date.today() - datetime.timedelta(days=1)
    else:
        try:
            rollup_date = datetime.date.fromisoformat(date)
        except ValueError:
            typer.echo(f"Error: invalid date '{date}' — expected YYYY-MM-DD", err=True)
            raise typer.Exit(code=1)

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_rollup(cfg, conn, rollup_date)
    finally:
        conn.close()

    typer.echo(
        f"Rollup complete: {result.rollup_date}, {result.rows_written} rows written"
    )


_VALID_REPORT_TARGETS = {"all", "dashboard", "projects", "history"}


@app.command()
def report(
    ctx: typer.Context,
    target: str = typer.Option(
        "all",
        "--target",
        help="Which dashboards to regenerate: all, dashboard, projects, history.",
    ),
    project_id: str = typer.Option(
        None,
        "--project-id",
        help="Regenerate a single project page (targeted; ignores --target).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Regenerate all reports and delete any generated files no longer valid.",
    ),
) -> None:
    """Render Jinja2 Markdown dashboards into the output directory."""
    if target not in _VALID_REPORT_TARGETS:
        typer.echo(
            f"Error: invalid target {target!r} — must be one of: "
            + ", ".join(sorted(_VALID_REPORT_TARGETS)),
            err=True,
        )
        raise typer.Exit(code=1)

    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_report(cfg, conn, target=target, project_id=project_id or None, force=force)
    finally:
        conn.close()

    typer.echo(
        f"Report complete: {result.files_written} files written, "
        f"{result.files_deleted} deleted ({result.target})"
    )


@search_app.command(name="index")
def search_index(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-index all eligible files regardless of search freshness state.",
    ),
    batch_size: int = typer.Option(
        None,
        "--batch-size",
        min=1,
        help="Override the configured SQLite commit batch size for this run.",
    ),
    model: str = typer.Option(
        None,
        "--model",
        help="Override the configured embedding model name for this run.",
    ),
) -> None:
    """Build or refresh the local search index."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_search_index_stage(
            cfg,
            conn,
            force=force,
            batch_size=batch_size,
            model_name=model,
        )
    finally:
        conn.close()

    typer.echo(
        "Search index complete: "
        f"{result.indexed_files} indexed, "
        f"{result.excluded_files} excluded, "
        f"{result.failed_files} failed, "
        f"{result.orphaned_files_purged} orphaned purged, "
        f"{result.chunks_written} chunks, "
        f"{result.vectors_written} vectors"
    )


def _emit_search_response(response) -> None:
    sys.stdout.write(response.model_dump_json())


def _raise_search_error(
    *,
    stdio: bool,
    exit_code: int,
    error_code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    response = build_error_response(
        error_code=error_code,
        message=message,
        details=details,
    )
    if stdio:
        _emit_search_response(response)
    else:
        typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=exit_code)


def _load_query_config(config_path: Path, *, stdio: bool):
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        _raise_search_error(
            stdio=stdio,
            exit_code=EXIT_CODE_INPUT_ERROR,
            error_code="config_error",
            message=f"config file not found: {config_path}",
            details={"config_path": str(config_path)},
        )
    except Exception as exc:
        _raise_search_error(
            stdio=stdio,
            exit_code=EXIT_CODE_INPUT_ERROR,
            error_code="config_error",
            message=f"invalid config: {exc}",
            details={"config_path": str(config_path)},
        )

    try:
        validate_config_paths(cfg)
    except ValueError as exc:
        _raise_search_error(
            stdio=stdio,
            exit_code=EXIT_CODE_INPUT_ERROR,
            error_code="config_error",
            message=str(exc),
            details={"config_path": str(config_path)},
        )
    return cfg


@search_app.command(name="query")
def search_query(
    ctx: typer.Context,
    query_text: str = typer.Argument(
        None,
        help="Human-mode search text. Omit when using --stdio.",
    ),
    stdio: bool = typer.Option(
        False,
        "--stdio",
        help="Read a JSON request from stdin and emit JSON-only to stdout.",
    ),
    search_mode: str = typer.Option(
        "hybrid",
        "--search-mode",
        help="Human-mode search strategy.",
    ),
    granularity: str = typer.Option(
        "chunk",
        "--granularity",
        help="Return chunk-level or file-level results in human mode.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum number of results to return in human mode.",
    ),
    min_score: float | None = typer.Option(
        None,
        "--min-score",
        help="Optional minimum score threshold for returned matches.",
    ),
    surrounding_chunks: int = typer.Option(
        0,
        "--surrounding-chunks",
        min=0,
        max=3,
        help="Number of adjacent chunks to include before and after a chunk hit.",
    ),
    include_content: bool = typer.Option(
        True,
        "--include-content/--no-include-content",
        help="Include matched content in human-mode output.",
    ),
) -> None:
    """Run a local search query in human or strict stdio mode."""
    cfg = _load_query_config(ctx.obj[_CONFIG_KEY], stdio=stdio)

    if stdio and query_text is not None:
        _raise_search_error(
            stdio=True,
            exit_code=EXIT_CODE_INPUT_ERROR,
            error_code="input_error",
            message="query argument cannot be combined with --stdio",
        )

    if not stdio and query_text is None:
        _raise_search_error(
            stdio=False,
            exit_code=EXIT_CODE_INPUT_ERROR,
            error_code="input_error",
            message="query text is required unless --stdio is used",
        )

    request_payload = None
    if stdio:
        try:
            request_payload = load_request_json(sys.stdin.read())
        except ValueError as exc:
            _raise_search_error(
                stdio=True,
                exit_code=EXIT_CODE_INPUT_ERROR,
                error_code="input_error",
                message=str(exc),
            )
    else:
        request_payload = {
            "query": query_text,
            "search_mode": search_mode,
            "tuning": {"min_score": min_score},
            "output": {
                "granularity": granularity,
                "limit": limit,
                "surrounding_chunks": surrounding_chunks,
                "include_content": include_content,
            },
        }

    if not cfg.db_path.exists():
        _raise_search_error(
            stdio=stdio,
            exit_code=EXIT_CODE_STORAGE_ERROR,
            error_code="database_error",
            message=f"search database file not found: {cfg.db_path}",
            details={"db_path": str(cfg.db_path)},
        )

    with isolated_search_logging(cfg):
        conn = get_connection(cfg.db_path)
        try:
            init_db(conn)
            outcome = run_search_request(cfg, conn, request_payload)
        finally:
            conn.close()

    if stdio:
        _emit_search_response(outcome.response)
        raise typer.Exit(code=outcome.exit_code)

    if outcome.exit_code == EXIT_CODE_STORAGE_ERROR:
        typer.echo(
            f"Search failed [database_error]: {outcome.response.error.message}",
            err=True,
        )
        raise typer.Exit(code=EXIT_CODE_STORAGE_ERROR)
    if outcome.exit_code == EXIT_CODE_EMBEDDING_ERROR:
        typer.echo(
            f"Search failed [embedding_error]: {outcome.response.error.message}",
            err=True,
        )
        raise typer.Exit(code=EXIT_CODE_EMBEDDING_ERROR)
    if outcome.exit_code == EXIT_CODE_INPUT_ERROR:
        typer.echo(
            f"Search failed [input_error]: {outcome.response.error.message}",
            err=True,
        )
        raise typer.Exit(code=EXIT_CODE_INPUT_ERROR)

    typer.echo(format_human_response(outcome.response))


@app.command(name="run-all")
def run_all(
    ctx: typer.Context,
    scan_projects_flag: bool = typer.Option(
        False,
        "--scan-projects",
        help="Run scan-projects --merge before sync (opt-in).",
    ),
    skip_rollup: bool = typer.Option(
        False,
        "--skip-rollup",
        help="Skip Stage IV rollup (for mid-day runs).",
    ),
    force_sync: bool = typer.Option(
        False,
        "--force-sync",
        help="Pass --force to the sync stage (re-hash all files).",
    ),
    force_report: bool = typer.Option(
        False,
        "--force-report",
        help="Pass --force to the report stage (regenerate all, delete stale files).",
    ),
    index_search: bool = typer.Option(
        False,
        "--index-search",
        help="Run search indexing after the core pipeline completes (opt-in).",
    ),
) -> None:
    """Run all pipeline stages in sequence: [scan-projects →] sync → parse → map-projects → rollup → report."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    rollup_date = datetime.date.today() - datetime.timedelta(days=1)

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)

        if scan_projects_flag:
            from matlock.stages.scan_projects import (  # noqa: PLC0415
                merge_into_config,
                scan_vault,
            )

            scanned, candidates = scan_vault(cfg)

            warned = False
            for sf in scanned:
                for w in sf.warnings:
                    if not warned:
                        typer.echo("Scan-projects warnings:", err=True)
                        warned = True
                    typer.echo(f"  [{sf.file_path}] {w}", err=True)

            merge_result = merge_into_config(ctx.obj[_CONFIG_KEY], candidates, scanned)
            typer.echo(
                f"Scan-projects: "
                f"{merge_result.super_projects_added} super-projects added, "
                f"{merge_result.super_projects_updated} updated, "
                f"{merge_result.super_projects_deleted} deleted; "
                f"{merge_result.projects_added} projects added, "
                f"{merge_result.projects_updated} updated, "
                f"{merge_result.projects_deleted} deleted"
            )

            # merge_into_config writes config.yaml; reload so map-projects uses merged data.
            cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

        sync_result = run_sync(cfg, conn, force=force_sync)
        typer.echo(
            f"Sync: {sync_result.inserted} inserted, {sync_result.updated} updated, "
            f"{sync_result.unchanged} unchanged, {sync_result.deleted} deleted"
        )

        parse_result = run_parse(cfg, conn)
        typer.echo(
            f"Parse: {parse_result.parsed} parsed, {parse_result.skipped} skipped, "
            f"{parse_result.tasks_inserted} tasks inserted, "
            f"{parse_result.tasks_deleted} tasks deleted"
        )

        map_result = run_map_projects(cfg, conn)
        typer.echo(
            f"Map-projects: {map_result.super_projects_written} super-projects, "
            f"{map_result.projects_written} projects, "
            f"{map_result.file_project_rows} file-project links"
        )

        if not skip_rollup:
            rollup_result = run_rollup(cfg, conn, rollup_date)
            typer.echo(
                f"Rollup: {rollup_result.rollup_date}, "
                f"{rollup_result.rows_written} rows written"
            )

        report_result = run_report(cfg, conn, target="all", project_id=None, force=force_report)
        typer.echo(
            f"Report: {report_result.files_written} files written, "
            f"{report_result.files_deleted} deleted ({report_result.target})"
        )

        if index_search:
            search_result = run_search_index_stage(cfg, conn)
            typer.echo(
                "Search index: "
                f"{search_result.indexed_files} indexed, "
                f"{search_result.excluded_files} excluded, "
                f"{search_result.failed_files} failed, "
                f"{search_result.orphaned_files_purged} orphaned purged, "
                f"{search_result.chunks_written} chunks, "
                f"{search_result.vectors_written} vectors"
            )
    finally:
        conn.close()

    typer.echo("run-all complete.")


@app.command(name="scan-projects")
def scan_projects(
    ctx: typer.Context,
    print_yaml: bool = typer.Option(
        False,
        "--print-yaml",
        "-p",
        help="Print discovered projects/super-projects as YAML (default when no mode given).",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        "-d",
        help="Print a human-readable diff against the current config.",
    ),
    merge: bool = typer.Option(
        False,
        "--merge",
        "-m",
        help="Merge scan results into the config file (full ins/upd/del). "
             "Creates a timestamped backup first.",
    ),
) -> None:
    """Scan the vault and reverse-engineer projects/super-projects from frontmatter."""
    modes_given = sum([print_yaml, diff, merge])
    if modes_given > 1:
        typer.echo(
            "Error: only one of --print-yaml, --diff, --merge may be given.", err=True
        )
        raise typer.Exit(code=1)
    if modes_given == 0:
        print_yaml = True  # default

    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    from matlock.stages.scan_projects import (  # noqa: PLC0415
        format_as_diff,
        format_as_yaml,
        merge_into_config,
        scan_vault,
    )

    scanned, candidates = scan_vault(cfg)

    if print_yaml:
        typer.echo(format_as_yaml(candidates, scanned))

    elif diff:
        typer.echo(format_as_diff(candidates, scanned, cfg))

    elif merge:
        config_path: Path = ctx.obj[_CONFIG_KEY]
        typer.echo(
            f"Warning: --merge will modify {config_path}. "
            "A timestamped backup will be created alongside the config file.",
            err=True,
        )
        result = merge_into_config(config_path, candidates, scanned)
        typer.echo(
            f"Merge complete: "
            f"{result.super_projects_added} super_project(s) added, "
            f"{result.super_projects_updated} updated, "
            f"{result.super_projects_deleted} deleted; "
            f"{result.projects_added} project(s) added, "
            f"{result.projects_updated} updated, "
            f"{result.projects_deleted} deleted. "
            f"Backup: {result.backup_path}."
        )

    # Emit all accumulated warnings to stderr, prefixed with the source file path
    warned = False
    for sf in scanned:
        for w in sf.warnings:
            if not warned:
                typer.echo("Scan warnings:", err=True)
                warned = True
            typer.echo(f"  [{sf.file_path}] {w}", err=True)


@app.command()
def server(
    ctx: typer.Context,
    debounce: int = typer.Option(
        None,
        "--debounce",
        help="Idle seconds before triggering report after file changes. "
             "Overrides config debounce_seconds.",
    ),
    scan_projects_flag: bool = typer.Option(
        False,
        "--scan-projects",
        help="Run scan-projects --merge once at startup before the watcher starts.",
    ),
    skip_rollup: bool = typer.Option(
        False,
        "--skip-rollup",
        help="Skip rollup in the nightly scheduled job.",
    ),
    force_sync: bool = typer.Option(
        False,
        "--force-sync",
        help="Run a full forced sync+parse once at startup before the watcher starts.",
    ),
    force_rollup: bool = typer.Option(
        False,
        "--force-rollup",
        help="Run rollup for today once at startup after any forced sync.",
    ),
    force_report: bool = typer.Option(
        False,
        "--force-report",
        help="Run a full forced report once at startup (after any startup sync/rollup).",
    ),
    index_search: bool = typer.Option(
        False,
        "--index-search",
        help="Run search indexing once at startup before the watcher starts.",
    ),
    index_search_continuous: bool = typer.Option(
        False,
        "--index-search-continuous",
        help="Continuously poll for stale search work in the background.",
    ),
) -> None:
    """Watch the vault and run pipeline stages automatically."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    if scan_projects_flag:
        from matlock.stages.scan_projects import (  # noqa: PLC0415
            merge_into_config,
            scan_vault,
        )

        scanned, candidates = scan_vault(cfg)

        warned = False
        for sf in scanned:
            for w in sf.warnings:
                if not warned:
                    typer.echo("Scan-projects warnings:", err=True)
                    warned = True
                typer.echo(f"  [{sf.file_path}] {w}", err=True)

        merge_result = merge_into_config(ctx.obj[_CONFIG_KEY], candidates, scanned)
        typer.echo(
            f"Scan-projects: "
            f"{merge_result.super_projects_added} super-projects added, "
            f"{merge_result.super_projects_updated} updated, "
            f"{merge_result.super_projects_deleted} deleted; "
            f"{merge_result.projects_added} projects added, "
            f"{merge_result.projects_updated} updated, "
            f"{merge_result.projects_deleted} deleted"
        )
        cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    run_server(
        cfg,
        debounce_seconds=debounce,
        skip_rollup=skip_rollup,
        force_sync=force_sync,
        force_rollup=force_rollup,
        force_report=force_report,
        index_search=index_search,
        index_search_continuous=index_search_continuous,
    )
