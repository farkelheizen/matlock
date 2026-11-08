from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .ingest import IngestionEngine, ProgressCallback
from .models import IngestFailure, ScanReport

_FULL_SCAN = object()


@dataclass(frozen=True)
class _PendingEvent:
    kind: str
    relative_path: str


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, vault_path: Path, events: queue.Queue[object]) -> None:
        self.vault_path = vault_path.resolve()
        self.events = events

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if event.is_directory:
            self.events.put(_FULL_SCAN)
            return
        if event.event_type == "moved":
            # Treat a move as removal of the old path and ingestion of the new one.
            source = getattr(event, "src_path", "")
            destination = getattr(event, "dest_path", "")
            self._enqueue_path("delete", source)
            self._enqueue_path("upsert", destination)
        else:
            kind = "delete" if event.event_type == "deleted" else "upsert"
            self._enqueue_path(kind, getattr(event, "src_path", ""))

    def _enqueue_path(self, kind: str, raw_path: str) -> None:
        try:
            path = Path(raw_path)
            relative = path.resolve(strict=False).relative_to(self.vault_path).as_posix()
        except (OSError, ValueError):
            return
        if path.suffix.lower() == ".md":
            self.events.put(_PendingEvent(kind, relative))


class VaultWatcher:
    def __init__(
        self,
        engine: IngestionEngine,
        on_scan: Callable[[ScanReport], None] | None = None,
        on_progress: ProgressCallback | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.engine = engine
        self.on_scan = on_scan
        self.on_progress = on_progress
        self.stop_event = stop_event or threading.Event()

    def run(self) -> None:
        events: queue.Queue[object] = queue.Queue()
        handler = _VaultEventHandler(self.engine.config.vault_path, events)
        observer = Observer()
        observer.schedule(
            handler, str(self.engine.config.vault_path), recursive=True
        )
        observer.start()
        try:
            self._report(self.engine.scan(self.on_progress))
            # Coalesce repeated events for each path before touching the database.
            pending: dict[str, tuple[object, float]] = {}
            next_reconciliation = (
                time.monotonic() + self.engine.config.reconcile_interval_seconds
            )
            while not self.stop_event.is_set():
                timeout = max(
                    0.0,
                    min(0.5, next_reconciliation - time.monotonic()),
                )
                try:
                    event = events.get(timeout=timeout)
                except queue.Empty:
                    event = None
                now = time.monotonic()
                if event is _FULL_SCAN:
                    pending["__full_scan__"] = (event, now + 0.2)
                elif isinstance(event, _PendingEvent):
                    pending[event.relative_path] = (event, now + 0.2)

                if now >= next_reconciliation:
                    pending["__full_scan__"] = (_FULL_SCAN, now)
                    next_reconciliation = (
                        now + self.engine.config.reconcile_interval_seconds
                    )

                due = [
                    key for key, (_, deadline) in pending.items() if deadline <= now
                ]
                for key in due:
                    action, _ = pending.pop(key)
                    if action is _FULL_SCAN:
                        self._report(self.engine.scan(self.on_progress))
                    elif isinstance(action, _PendingEvent):
                        if action.kind == "delete":
                            self.engine.database.remove_source(action.relative_path)
                        else:
                            result = self.engine.ingest_with_retries(action.relative_path)
                            if self.on_progress is not None:
                                self.on_progress(
                                    0, 0, action.relative_path, result
                                )
                            if isinstance(result, IngestFailure):
                                print(
                                    f"ERROR {result.relative_path} [{result.stage}]: "
                                    f"{result.error}"
                                )
        finally:
            observer.stop()
            observer.join()

    def _report(self, report: ScanReport) -> None:
        if self.on_scan is not None:
            self.on_scan(report)
