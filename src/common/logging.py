"""Consistent console and file logging."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(root: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("la_housing")
    logger.handlers.clear()
    logger.setLevel(level.upper())
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    log_path = root / "output" / "pipeline.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
