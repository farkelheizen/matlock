from __future__ import annotations

import logging
import sys
from pathlib import Path

from matlock.config import MatlockConfig
from matlock.search.logging import isolated_search_logging, resolve_search_log_path


def _config(tmp_path: Path, *, log_path: Path | None = None) -> MatlockConfig:
    base_dir = tmp_path / "vault"
    base_dir.mkdir(exist_ok=True)
    return MatlockConfig(
        base_directory=base_dir,
        db_path=tmp_path / "matlock.db",
        output_directory=tmp_path / "_Matlock",
        log_path=log_path,
    )


def test_isolated_search_logging_routes_warnings_to_file_and_errors_to_stderr(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = _config(tmp_path)
    logger = logging.getLogger("matlock.search.logging-test")

    with isolated_search_logging(config) as log_path:
        logger.warning("warning message")
        logger.error("error message")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error message" in captured.err
    log_text = log_path.read_text(encoding="utf-8")
    assert "warning message" in log_text
    assert "error message" in log_text


def test_isolated_search_logging_uses_dedicated_sibling_log_file(tmp_path: Path) -> None:
    config = _config(tmp_path, log_path=tmp_path / "logs" / "matlock.log")

    expected_path = tmp_path / "logs" / "search.log"
    assert resolve_search_log_path(config) == expected_path

    with isolated_search_logging(config) as log_path:
        logging.getLogger("matlock.search.logging-test").info("hello")

    assert log_path == expected_path
    assert expected_path.exists()
    assert "hello" in expected_path.read_text(encoding="utf-8")


def test_isolated_search_logging_restores_existing_root_handlers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = logging.getLogger()
    original_handler = logging.StreamHandler(sys.stdout)
    root.addHandler(original_handler)
    try:
        with isolated_search_logging(config):
            assert original_handler not in logging.getLogger().handlers
        assert original_handler in logging.getLogger().handlers
    finally:
        root.removeHandler(original_handler)