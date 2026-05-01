"""Tests for matlock/server.py — server daemon unit tests."""
from __future__ import annotations

import datetime
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from matlock.config import load_config
from matlock.db import get_connection, init_db
from matlock.server import (
    _VaultEventHandler,
    _debouncer_thread,
    _scheduler_thread,
    _seconds_until_midnight,
    run_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(cfg_path: Path, base_dir: Path, db_path: Path, out_dir: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(out_dir),
        "debounce_seconds": 1,
    }
    with cfg_path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _make_config(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out_dir = tmp_path / "_Matlock"
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "matlock.db"
    _write_config(cfg_path, vault, db_path, out_dir)
    return load_config(cfg_path), vault, db_path, out_dir


# ---------------------------------------------------------------------------
# _seconds_until_midnight
# ---------------------------------------------------------------------------


class TestSecondsUntilMidnight:
    def test_returns_positive(self):
        result = _seconds_until_midnight()
        assert result > 0

    def test_less_than_one_day(self):
        result = _seconds_until_midnight()
        assert result <= 86461  # at most 24h + 1min

    def test_mocked_time(self):
        """When it is 23:59, next midnight+1m is about 2 minutes away."""
        fake_now = datetime.datetime(2026, 4, 27, 23, 59, 0)
        with patch("matlock.server.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.timedelta = datetime.timedelta
            result = _seconds_until_midnight()
        assert 60 < result < 130


# ---------------------------------------------------------------------------
# _VaultEventHandler
# ---------------------------------------------------------------------------


class TestVaultEventHandler:
    def _make_handler(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()
        dirty = set()
        dirty_lock = threading.Lock()
        dirty_event = threading.Event()
        stop_event = threading.Event()
        handler = _VaultEventHandler(
            config=config,
            dirty_projects=dirty,
            dirty_lock=dirty_lock,
            dirty_event=dirty_event,
            stop_event=stop_event,
        )
        return handler, config, vault, db_path, dirty, dirty_event

    def test_ignores_output_directory(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        out_file = str(config.output_directory / "000_Daily_Dashboard.md")
        event = SimpleNamespace(
            is_directory=False,
            src_path=out_file,
            event_type="modified",
        )
        handler._handle(event)
        assert not dirty_event.is_set()

    def test_ignores_non_md_files(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        event = SimpleNamespace(
            is_directory=False,
            src_path=str(vault / "image.png"),
            event_type="modified",
        )
        handler._handle(event)
        assert not dirty_event.is_set()

    def test_ignores_directory_events(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        event = SimpleNamespace(
            is_directory=True,
            src_path=str(vault / "subdir"),
            event_type="created",
        )
        handler._handle(event)
        assert not dirty_event.is_set()

    def test_md_event_sets_dirty_event(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        md = vault / "note.md"
        md.write_text("# Note\n- [ ] Task\n", encoding="utf-8")
        event = SimpleNamespace(
            is_directory=False,
            src_path=str(md),
            event_type="modified",
        )
        handler._handle(event)
        assert dirty_event.is_set()

    def test_md_event_syncs_and_parses(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        md = vault / "note.md"
        md.write_text("# Note\n- [ ] Task\n", encoding="utf-8")
        event = SimpleNamespace(
            is_directory=False,
            src_path=str(md),
            event_type="created",
        )
        handler._handle(event)
        conn = get_connection(db_path)
        # sync stores paths relative to base_directory
        row = conn.execute("SELECT COUNT(*) as n FROM file").fetchone()
        conn.close()
        assert row["n"] >= 1

    def test_affected_projects_added_to_dirty(self, tmp_path: Path):
        handler, config, vault, db_path, dirty, dirty_event = self._make_handler(tmp_path)
        md = vault / "work.md"
        md.write_text("# Work\n- [ ] Task\n", encoding="utf-8")
        # Pre-seed a file_project link
        conn = get_connection(db_path)
        init_db(conn)
        conn.execute("INSERT OR REPLACE INTO project (project_id, title) VALUES (?, ?)", ("p1", "P1"))
        conn.execute("INSERT OR REPLACE INTO file (file_path, sha256, needs_parsing) VALUES (?, ?, ?)", (str(md), "abc", 0))
        conn.execute("INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)", (str(md), "p1"))
        conn.commit()
        conn.close()
        event = SimpleNamespace(
            is_directory=False,
            src_path=str(md),
            event_type="modified",
        )
        handler._handle(event)
        assert "p1" in dirty


# ---------------------------------------------------------------------------
# _debouncer_thread
# ---------------------------------------------------------------------------


class TestDebouncerThread:
    def test_runs_report_after_quiet_period(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        dirty: set[str] = set()
        dirty_lock = threading.Lock()
        dirty_event = threading.Event()
        stop_event = threading.Event()

        report_calls: list = []

        def fake_report(cfg, conn, **kwargs):
            report_calls.append(kwargs)
            from matlock.stages.report import ReportResult
            return ReportResult(files_written=0, target="all")

        with patch("matlock.server.run_report", side_effect=fake_report):
            t = threading.Thread(
                target=_debouncer_thread,
                args=(config, 0, dirty, dirty_lock, dirty_event, stop_event),
                daemon=True,
            )
            t.start()
            # Signal that something is dirty
            with dirty_lock:
                dirty.add("proj1")
                dirty_event.set()
            # Give the debouncer time to fire (debounce=0s)
            time.sleep(0.5)
            stop_event.set()
            t.join(timeout=3)

        assert len(report_calls) >= 1

    def test_clears_dirty_set_after_report(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        dirty: set[str] = {"proj1", "proj2"}
        dirty_lock = threading.Lock()
        dirty_event = threading.Event()
        dirty_event.set()
        stop_event = threading.Event()

        with patch("matlock.server.run_report") as mock_report:
            from matlock.stages.report import ReportResult
            mock_report.return_value = ReportResult(files_written=0, target="all")
            t = threading.Thread(
                target=_debouncer_thread,
                args=(config, 0, dirty, dirty_lock, dirty_event, stop_event),
                daemon=True,
            )
            t.start()
            time.sleep(0.3)
            stop_event.set()
            t.join(timeout=3)

        assert len(dirty) == 0

    def test_stops_on_stop_event(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        dirty: set[str] = set()
        dirty_lock = threading.Lock()
        dirty_event = threading.Event()
        stop_event = threading.Event()
        stop_event.set()  # stop immediately

        t = threading.Thread(
            target=_debouncer_thread,
            args=(config, 1, dirty, dirty_lock, dirty_event, stop_event),
            daemon=True,
        )
        t.start()
        t.join(timeout=3)
        assert not t.is_alive()


# ---------------------------------------------------------------------------
# _scheduler_thread
# ---------------------------------------------------------------------------


class TestSchedulerThread:
    def test_stops_on_stop_event(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        stop_event = threading.Event()
        stop_event.set()

        t = threading.Thread(
            target=_scheduler_thread,
            args=(config, stop_event),
            daemon=True,
        )
        t.start()
        t.join(timeout=3)
        assert not t.is_alive()

    def test_runs_rollup_and_report_at_midnight(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_event = threading.Event()
        rollup_calls: list = []
        report_calls: list = []

        from matlock.stages.rollup import RollupResult
        from matlock.stages.report import ReportResult

        def fake_rollup(cfg, conn, rollup_date):
            rollup_calls.append(rollup_date)
            return RollupResult(rollup_date=rollup_date, rows_written=0)

        def fake_report(cfg, conn, **kwargs):
            report_calls.append(kwargs)
            return ReportResult(files_written=0, target="all")

        # Patch _seconds_until_midnight to return near-zero so thread fires immediately
        with patch("matlock.server._seconds_until_midnight", return_value=0.05):
            with patch("matlock.server.run_rollup", side_effect=fake_rollup):
                with patch("matlock.server.run_report", side_effect=fake_report):
                    t = threading.Thread(
                        target=_scheduler_thread,
                        args=(config, stop_event),
                        daemon=True,
                    )
                    t.start()
                    time.sleep(0.5)
                    stop_event.set()
                    t.join(timeout=3)

        assert len(rollup_calls) >= 1
        assert len(report_calls) >= 1


# ---------------------------------------------------------------------------
# run_server (integration — mocked observer and short-circuit)
# ---------------------------------------------------------------------------


class TestRunServer:
    def test_starts_and_stops_cleanly(self, tmp_path: Path):
        """run_server should start and stop without error when SIGINT is simulated."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_calls: list = []

        class FakeObserver:
            def schedule(self, *a, **kw): pass
            def start(self): pass
            def stop(self): stop_calls.append(True)
            def join(self, *a, **kw): pass

        # Simulate KeyboardInterrupt after one loop iteration
        original_sleep = time.sleep
        call_count = [0]

        def patched_sleep(n):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise KeyboardInterrupt
            original_sleep(min(n, 0.05))

        with patch("matlock.server.Observer", return_value=FakeObserver()):
            with patch("matlock.server.time.sleep", side_effect=patched_sleep):
                run_server(config, debounce_seconds=1)

        assert len(stop_calls) >= 1

    def test_debounce_override(self, tmp_path: Path):
        """debounce_seconds parameter overrides config value."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        captured_debounce: list = []
        original_thread = threading.Thread

        def patched_thread(*args, **kwargs):
            if kwargs.get("name") == "matlock-debouncer":
                # args[1] is the args tuple passed to _debouncer_thread
                captured_debounce.append(kwargs.get("args", args))
            return original_thread(*args, **kwargs)

        class FakeObserver:
            def schedule(self, *a, **kw): pass
            def start(self): pass
            def stop(self): pass
            def join(self, *a, **kw): pass

        call_count = [0]
        original_sleep = time.sleep

        def patched_sleep(n):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise KeyboardInterrupt
            original_sleep(min(n, 0.05))

        with patch("matlock.server.Observer", return_value=FakeObserver()):
            with patch("matlock.server.time.sleep", side_effect=patched_sleep):
                with patch("matlock.server.threading.Thread", side_effect=patched_thread):
                    try:
                        run_server(config, debounce_seconds=42)
                    except Exception:
                        pass

        # The debounce value 42 should appear in the debouncer thread args
        assert any(str(42) in str(item) for item in captured_debounce)


# ---------------------------------------------------------------------------
# _scheduler_thread — skip_rollup
# ---------------------------------------------------------------------------


class TestSchedulerThreadSkipRollup:
    def test_skip_rollup_suppresses_rollup(self, tmp_path: Path):
        """When skip_rollup=True the scheduler skips run_rollup but still runs report."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_event = threading.Event()
        rollup_calls: list = []
        report_calls: list = []

        from matlock.stages.rollup import RollupResult
        from matlock.stages.report import ReportResult

        def fake_rollup(cfg, conn, rollup_date):
            rollup_calls.append(rollup_date)
            return RollupResult(rollup_date=rollup_date, rows_written=0)

        def fake_report(cfg, conn, **kwargs):
            report_calls.append(kwargs)
            return ReportResult(files_written=0, target="all")

        with patch("matlock.server._seconds_until_midnight", return_value=0.05):
            with patch("matlock.server.run_rollup", side_effect=fake_rollup):
                with patch("matlock.server.run_report", side_effect=fake_report):
                    t = threading.Thread(
                        target=_scheduler_thread,
                        args=(config, stop_event, True),  # skip_rollup=True
                        daemon=True,
                    )
                    t.start()
                    time.sleep(0.5)
                    stop_event.set()
                    t.join(timeout=3)

        assert len(rollup_calls) == 0
        assert len(report_calls) >= 1

    def test_skip_rollup_false_still_runs_rollup(self, tmp_path: Path):
        """When skip_rollup=False (default) the scheduler runs rollup as normal."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_event = threading.Event()
        rollup_calls: list = []

        from matlock.stages.rollup import RollupResult
        from matlock.stages.report import ReportResult

        with patch("matlock.server._seconds_until_midnight", return_value=0.05):
            with patch("matlock.server.run_rollup", return_value=RollupResult(
                rollup_date=datetime.date.today(), rows_written=0
            )) as mock_rollup:
                with patch("matlock.server.run_report", return_value=ReportResult(
                    files_written=0, target="all"
                )):
                    t = threading.Thread(
                        target=_scheduler_thread,
                        args=(config, stop_event, False),  # skip_rollup=False
                        daemon=True,
                    )
                    t.start()
                    time.sleep(0.5)
                    stop_event.set()
                    t.join(timeout=3)

        assert mock_rollup.call_count >= 1


# ---------------------------------------------------------------------------
# run_server — startup flags
# ---------------------------------------------------------------------------


class TestRunServerStartupFlags:
    def _make_fake_observer(self):
        class FakeObserver:
            def schedule(self, *a, **kw): pass
            def start(self): pass
            def stop(self): pass
            def join(self, *a, **kw): pass
        return FakeObserver()

    def _patched_sleep(self, call_limit: int, original_sleep=time.sleep):
        call_count = [0]

        def _sleep(n):
            call_count[0] += 1
            if call_count[0] >= call_limit:
                raise KeyboardInterrupt
            original_sleep(min(n, 0.05))
        return _sleep

    def test_force_sync_runs_sync_and_parse_at_startup(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        sync_calls: list = []
        parse_calls: list = []

        from matlock.stages.sync import SyncResult
        from matlock.stages.parse import ParseResult
        from matlock.stages.report import ReportResult

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.run_sync",
                           return_value=SyncResult(inserted=0, updated=0, unchanged=0, deleted=0)) as mock_sync:
                    with patch("matlock.server.run_parse",
                               return_value=ParseResult(parsed=0, skipped=0, tasks_inserted=0, tasks_deleted=0)) as mock_parse:
                        with patch("matlock.server.run_report",
                                   return_value=ReportResult(files_written=0, target="all")):
                            try:
                                run_server(config, debounce_seconds=1, force_sync=True)
                            except KeyboardInterrupt:
                                pass

        assert mock_sync.call_count >= 1
        assert mock_parse.call_count >= 1
        # Verify force=True was passed to run_sync
        _, kwargs = mock_sync.call_args_list[0]
        assert kwargs.get("force") is True or mock_sync.call_args_list[0].args[2] is True

    def test_force_report_runs_report_at_startup(self, tmp_path: Path):
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        from matlock.stages.report import ReportResult

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.run_report",
                           return_value=ReportResult(files_written=0, target="all")) as mock_report:
                    try:
                        run_server(config, debounce_seconds=1, force_report=True)
                    except KeyboardInterrupt:
                        pass

        assert mock_report.call_count >= 1
        # Verify force=True was passed
        call_kwargs = mock_report.call_args_list[0][1]
        assert call_kwargs.get("force") is True

    def test_no_startup_sync_when_flags_not_set(self, tmp_path: Path):
        """Without flags, run_sync and run_parse are NOT called at startup."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.run_sync") as mock_sync:
                    with patch("matlock.server.run_parse") as mock_parse:
                        try:
                            run_server(config, debounce_seconds=1)
                        except KeyboardInterrupt:
                            pass

        assert mock_sync.call_count == 0
        assert mock_parse.call_count == 0

    def test_skip_rollup_passed_to_scheduler_thread(self, tmp_path: Path):
        """skip_rollup=True is threaded through to _scheduler_thread args."""
        config, vault, db_path, out_dir = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        captured_scheduler_args: list = []
        original_thread = threading.Thread

        def patched_thread(*args, **kwargs):
            if kwargs.get("name") == "matlock-scheduler":
                captured_scheduler_args.append(kwargs.get("args", ()))
            return original_thread(*args, **kwargs)

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.threading.Thread", side_effect=patched_thread):
                    try:
                        run_server(config, debounce_seconds=1, skip_rollup=True)
                    except (KeyboardInterrupt, Exception):
                        pass

        # The scheduler args tuple should contain True (skip_rollup)
        assert any(True in args for args in captured_scheduler_args)
