"""
Matlock logging configuration.

Call ``setup_logging(config)`` once at CLI startup to configure the root
logger.  When ``config.log_path`` is set, a ``RotatingFileHandler`` is added;
otherwise only a ``StreamHandler`` (stderr) is used.
"""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matlock.config import MatlockConfig

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(config: "MatlockConfig") -> None:
    """Configure the root logger based on *config*.

    - Always adds a ``StreamHandler`` to stderr at WARNING level so errors
      surface in the terminal even when file logging is enabled.
    - When ``config.log_path`` is set, adds a ``RotatingFileHandler`` at DEBUG
      level; the parent directory is created if it does not already exist.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console handler — warnings and above
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler — full debug log (only when configured)
    if config.log_path is not None:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            config.log_path,
            maxBytes=config.log_max_bytes,
            backupCount=config.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
