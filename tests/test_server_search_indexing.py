"""Focused tests for server search indexing orchestration."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import yaml

from matlock.config import load_config
from matlock.db import get_connection, init_db
from matlock.search.indexer import SearchIndexingResult
from matlock.server import _search_indexer_thread, run_server


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
    return load_config(cfg_path), db_path


class TestSearchIndexerThread:
    def test_run_immediately_executes_before_waiting(self, tmp_path: Path):
        config, db_path = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_event = threading.Event()
        calls: list[str] = []

        def fake_run_search_index_stage(cfg, conn, **kwargs):
            calls.append("indexed")
            stop_event.set()
            return SearchIndexingResult()

        with patch("matlock.server.run_search_index_stage", side_effect=fake_run_search_index_stage):
            _search_indexer_thread(
                config,
                stop_event,
                interval_seconds=10,
                run_immediately=True,
            )

        assert calls == ["indexed"]

    def test_interval_loop_repeats_until_stopped(self, tmp_path: Path):
        config, db_path = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        stop_event = threading.Event()
        calls: list[str] = []

        def fake_run_search_index_stage(cfg, conn, **kwargs):
            calls.append("indexed")
            if len(calls) >= 2:
                stop_event.set()
            return SearchIndexingResult()

        with patch("matlock.server.run_search_index_stage", side_effect=fake_run_search_index_stage):
            thread = threading.Thread(
                target=_search_indexer_thread,
                kwargs={
                    "config": config,
                    "stop_event": stop_event,
                    "interval_seconds": 0,
                    "run_immediately": True,
                },
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3)

        assert len(calls) >= 2


class TestRunServerSearchIndexing:
    def _make_fake_observer(self):
        class FakeObserver:
            def schedule(self, *args, **kwargs):
                return None

            def start(self):
                return None

            def stop(self):
                return None

            def join(self, *args, **kwargs):
                return None

        return FakeObserver()

    def _patched_sleep(self, call_limit: int, original_sleep=time.sleep):
        call_count = [0]

        def _sleep(seconds: float):
            call_count[0] += 1
            if call_count[0] >= call_limit:
                raise KeyboardInterrupt
            original_sleep(min(seconds, 0.05))

        return _sleep

    def test_startup_index_search_runs_once(self, tmp_path: Path):
        config, db_path = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch(
                    "matlock.server.run_search_index_stage",
                    return_value=SearchIndexingResult(indexed_files=1),
                ) as mock_index:
                    try:
                        run_server(config, debounce_seconds=1, index_search=True)
                    except KeyboardInterrupt:
                        pass

        assert mock_index.call_count == 1

    def test_continuous_indexing_thread_is_started(self, tmp_path: Path):
        config, db_path = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        captured_search_args: list[dict[str, object]] = []
        original_thread = threading.Thread

        def patched_thread(*args, **kwargs):
            if kwargs.get("name") == "matlock-search-indexer":
                captured_search_args.append(kwargs)
            return original_thread(*args, **kwargs)

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.threading.Thread", side_effect=patched_thread):
                    try:
                        run_server(config, debounce_seconds=3, index_search_continuous=True)
                    except (KeyboardInterrupt, Exception):
                        pass

        assert len(captured_search_args) == 1
        kwargs = captured_search_args[0]["kwargs"]
        assert kwargs["interval_seconds"] == 3
        assert kwargs["run_immediately"] is True

    def test_continuous_indexing_skips_duplicate_immediate_run_when_startup_enabled(self, tmp_path: Path):
        config, db_path = _make_config(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        conn.close()

        captured_search_args: list[dict[str, object]] = []
        original_thread = threading.Thread

        def patched_thread(*args, **kwargs):
            if kwargs.get("name") == "matlock-search-indexer":
                captured_search_args.append(kwargs)
            return original_thread(*args, **kwargs)

        with patch("matlock.server.Observer", return_value=self._make_fake_observer()):
            with patch("matlock.server.time.sleep", side_effect=self._patched_sleep(2)):
                with patch("matlock.server.threading.Thread", side_effect=patched_thread):
                    with patch(
                        "matlock.server.run_search_index_stage",
                        return_value=SearchIndexingResult(indexed_files=1),
                    ):
                        try:
                            run_server(
                                config,
                                debounce_seconds=2,
                                index_search=True,
                                index_search_continuous=True,
                            )
                        except (KeyboardInterrupt, Exception):
                            pass

        kwargs = captured_search_args[0]["kwargs"]
        assert kwargs["run_immediately"] is False