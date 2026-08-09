from __future__ import annotations

import logging
import logging.handlers
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from matlock.config import MatlockConfig

SEARCH_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(module)s] - %(message)s"
SEARCH_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
SEARCH_LOG_MAX_BYTES = 5_000_000
SEARCH_LOG_BACKUP_COUNT = 3


def resolve_search_log_path(config: MatlockConfig) -> Path:
    if config.log_path is not None:
        return config.log_path.parent / "search.log"
    return Path.home() / ".matlock" / "logs" / "search.log"


@contextmanager
def isolated_search_logging(config: MatlockConfig) -> Iterator[Path]:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    search_log_path = resolve_search_log_path(config)
    search_log_path.parent.mkdir(parents=True, exist_ok=True)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(SEARCH_LOG_FORMAT, datefmt=SEARCH_DATE_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        search_log_path,
        maxBytes=SEARCH_LOG_MAX_BYTES,
        backupCount=SEARCH_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    try:
        yield search_log_path
    finally:
        root.removeHandler(file_handler)
        root.removeHandler(stderr_handler)
        file_handler.close()
        stderr_handler.close()
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)