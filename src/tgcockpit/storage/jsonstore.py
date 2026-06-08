"""Атомарная запись/чтение JSON в ``channels/<name>/data/``.

Атомарность через tempfile + ``os.replace`` (rename атомарен на одной ФС): читатель
никогда не увидит полузаписанный файл, фетч не бьётся при прерывании на полпути.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    """Прочитать JSON; вернуть ``default``, если файла нет ИЛИ он повреждён.

    Битый state/pending не должен ронять бота — деградируем к default, а не падаем.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        from ..util.logging import get_logger

        get_logger("storage").warning("повреждён JSON %s — использую default: %s", path, e)
        return default


def write_json(path: Path, data: Any) -> Path:
    """Атомарно записать ``data`` в ``path`` (UTF-8, отступы, не-ASCII как есть)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    # tempfile в той же папке → rename гарантированно на той же ФС
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # атомарная замена
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
