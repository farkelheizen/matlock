"""Matlock server daemon.

Combines three triggers:
1. File watcher  — watchdog monitors base_directory; on change: sync + parse, mark projects dirty.
2. Debouncer     — after debounce_seconds of quiet, runs report for all dirty projects.
3. Scheduler     — at 00:01 each night: runs rollup then a full report rebuild.

Usage (via CLI):
    matlock server [--debounce SECONDS]
"""

from __future__ import annotations

import datetime
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from matlock.config import MatlockConfig
from matlock.db import get_connection, init_db
from matlock.stages.map_projects import run_map_projects
from matlock.stages.parse import run_parse
from matlock.stages.report import run_report
from matlock.stages.rollup import run_rollup
from matlock.stages.sync import run_sync

# One lock shared across all triggers to prevent concurrent stage execution.
_pipeline_lock = threading.Lock()


# ---------------------------------------------------------------------------
# File-system event handler
# ---------------------------------------------------------------------------


class _VaultEventHandler(FileSystemEventHandler):
    """Handle create/modify/delete events under base_directory."""

    def __init__(
        self,
        config: MatlockConfig,
        dirty_projects: set[str],
        dirty_lock: threading.Lock,
        dirty_event: threading.Event,
        stop_event: threading.Event,
    ) -> None:
        super().__init__()
        self._config = config
        self._dirty_projects = dirty_projects
        self._dirty_lock = dirty_lock
        self._dirty_event = dirty_event
        self._stop_event = stop_event

        # Build the set of path prefixes to ignore (output_directory + ignore_dirs)
        base = Path(config.base_directory)
        self._ignored_prefixes: list[str] = [
            str(config.output_directory).rstrip("/") + "/",
        ]
        for d in config.ignore_dirs:
            self._ignored_prefixes.append(str(base / d).rstrip("/") + "/")

    def _should_ignore(self, path: str) -> bool:
        for prefix in self._ignored_prefixes:
            if path.startswith(prefix):
                return True
        return False

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = str(event.src_path)
        if not src.endswith(".md"):
            return
        if self._should_ignore(src):
            return

        typer_echo(f"[watcher] {event.event_type}: {src}")

        with _pipeline_lock:
            conn = get_connection(self._config.db_path)
            try:
                init_db(conn)
                run_sync(self._config, conn)
                run_parse(self._config, conn)
                # Determine which projects are affected by this file
                rows = conn.execute(
                    "SELECT project_id FROM file_project WHERE file_path = ?",
                    (src,),
                ).fetchall()
                affected = {row["project_id"] for row in rows}
            finally:
                conn.close()

        with self._dirty_lock:
            self._dirty_projects.update(affected)
            # Even if no projects matched, signal so the debouncer wakes
            self._dirty_event.set()

    on_created = _handle
    on_modified = _handle
    on_deleted = _handle


# ---------------------------------------------------------------------------
# Debouncer thread
# ---------------------------------------------------------------------------


def _debouncer_thread(
    config: MatlockConfig,
    debounce_seconds: int,
    dirty_projects: set[str],
    dirty_lock: threading.Lock,
    dirty_event: threading.Event,
    stop_event: threading.Event,
) -> None:
    """Wait for the dirty_event, sleep debounce_seconds, then run report."""
    while not stop_event.is_set():
        # Block until something is dirty or we're asked to stop
        dirty_event.wait(timeout=1.0)
        if stop_event.is_set():
            break
        if not dirty_event.is_set():
            continue

        # There is at least one dirty project — wait for the quiet period
        time.sleep(debounce_seconds)

        with dirty_lock:
            n = len(dirty_projects)
            dirty_projects.clear()
            dirty_event.clear()

        typer_echo(f"[debouncer] report triggered (was dirty: {n} projects)")
        with _pipeline_lock:
            conn = get_connection(config.db_path)
            try:
                init_db(conn)
                run_map_projects(config, conn)
                run_report(config, conn, target="all")
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# Scheduler thread
# ---------------------------------------------------------------------------


def _seconds_until_midnight() -> float:
    """Return seconds until 00:01 local time."""
    now = datetime.datetime.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0
    )
    delta = (tomorrow - now).total_seconds()
    return max(delta, 1.0)


def _scheduler_thread(
    config: MatlockConfig,
    stop_event: threading.Event,
    skip_rollup: bool = False,
) -> None:
    """At 00:01 each night, run rollup then a full report."""
    while not stop_event.is_set():
        wait = _seconds_until_midnight()
        # Sleep in small chunks so we can react to stop_event promptly
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if stop_event.is_set():
                return
            time.sleep(min(30.0, deadline - time.monotonic()))

        if stop_event.is_set():
            return

        typer_echo("[scheduler] nightly rollup+report starting")
        rollup_date = datetime.date.today() - datetime.timedelta(days=1)
        with _pipeline_lock:
            conn = get_connection(config.db_path)
            try:
                init_db(conn)
                if not skip_rollup:
                    run_rollup(config, conn, rollup_date)
                run_map_projects(config, conn)
                run_report(config, conn, target="all")
            finally:
                conn.close()
        typer_echo("[scheduler] nightly rollup+report complete")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def typer_echo(message: str) -> None:
    """Thin wrapper so tests can patch stdout output."""
    print(message, flush=True)


def run_server(
    config: MatlockConfig,
    debounce_seconds: int | None = None,
    skip_rollup: bool = False,
    force_sync: bool = False,
    force_report: bool = False,
) -> None:
    """Start the file watcher, debouncer, and scheduler. Blocks until SIGINT.

    Parameters
    ----------
    config:
        Validated ``MatlockConfig`` instance.
    debounce_seconds:
        Idle window (seconds) before triggering report after file changes.
        Overrides ``config.debounce_seconds`` when provided.
    skip_rollup:
        When True, the nightly scheduler skips the rollup step.
    force_sync:
        When True, run a full forced sync+parse once at startup before the watcher starts.
    force_report:
        When True, run a full forced report once at startup (after any startup sync).
    """
    effective_debounce = debounce_seconds if debounce_seconds is not None else config.debounce_seconds

    # Startup block: one-time operations before the watcher starts
    if force_sync or force_report:
        startup_conn = get_connection(config.db_path)
        try:
            init_db(startup_conn)
            if force_sync:
                typer_echo("[startup] forced sync+parse starting")
                run_sync(config, startup_conn, force=True)
                run_parse(config, startup_conn)
                typer_echo("[startup] forced sync+parse complete")
            if force_report:
                typer_echo("[startup] forced report starting")
                run_map_projects(config, startup_conn)
                run_report(config, startup_conn, target="all", force=True)
                typer_echo("[startup] forced report complete")
        finally:
            startup_conn.close()

    # Shared state
    dirty_projects: set[str] = set()
    dirty_lock = threading.Lock()
    dirty_event = threading.Event()
    stop_event = threading.Event()

    # File watcher
    handler = _VaultEventHandler(
        config=config,
        dirty_projects=dirty_projects,
        dirty_lock=dirty_lock,
        dirty_event=dirty_event,
        stop_event=stop_event,
    )
    observer = Observer()
    observer.schedule(handler, str(config.base_directory), recursive=True)
    observer.start()

    # Debouncer thread
    debouncer = threading.Thread(
        target=_debouncer_thread,
        args=(config, effective_debounce, dirty_projects, dirty_lock, dirty_event, stop_event),
        daemon=True,
        name="matlock-debouncer",
    )
    debouncer.start()

    # Scheduler thread
    scheduler = threading.Thread(
        target=_scheduler_thread,
        args=(config, stop_event, skip_rollup),
        daemon=True,
        name="matlock-scheduler",
    )
    scheduler.start()

    typer_echo(
        f"[server] watching {config.base_directory} "
        f"(debounce={effective_debounce}s) — press Ctrl+C to stop"
    )

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        typer_echo("[server] shutting down…")
        stop_event.set()
        observer.stop()
        observer.join()
        debouncer.join(timeout=effective_debounce + 2)
        scheduler.join(timeout=2)
        typer_echo("[server] stopped.")
