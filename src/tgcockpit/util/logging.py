"""Логирование: человекочитаемо в консоль (rich), плюс файл в ``logs/``."""

from __future__ import annotations

import logging
from functools import lru_cache

from rich.logging import RichHandler

from .. import paths

_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


@lru_cache
def _configure() -> None:
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(logs / "tgcockpit.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FMT))
    file_handler.setLevel(logging.DEBUG)

    console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    console.setLevel(logging.INFO)

    root = logging.getLogger("tgcockpit")
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [file_handler, console]
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Логгер под неймспейсом пакета. Первая выдача настраивает хендлеры."""
    _configure()
    return logging.getLogger(f"tgcockpit.{name}")
