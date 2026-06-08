"""Очередь действий, ожидающих подтверждения пользователя.

Защита от автопубликации: когда агент (через мост) вызывает публикующую команду
(`post`/`schedule`/`message edit/delete`/`poll`/`group reply`), она НЕ выполняется,
а кладётся сюда. Бот показывает кнопку — и действие выполняется только по нажатию
пользователя. Так Claude физически не может опубликовать что-либо без апрува.

Гейт включается, когда выставлен ``TGCOCKPIT_BRIDGE=1`` (его ставит мост) и НЕ
выставлен ``TGCOCKPIT_CONFIRM=1``. В терминале (без BRIDGE) команды выполняются
сразу — там апрувер сам человек.
"""

from __future__ import annotations

import os
import secrets as _secrets
from typing import Any

from . import paths
from .storage import jsonstore
from .util.logging import get_logger

log = get_logger("pending")


def needs_approval() -> bool:
    """Нужно ли ставить действие на подтверждение (вызов из-под моста без подтверждения)."""
    return os.environ.get("TGCOCKPIT_BRIDGE") == "1" and os.environ.get("TGCOCKPIT_CONFIRM") != "1"


def _load() -> list[dict[str, Any]]:
    return jsonstore.read_json(paths.pending_file(), default=[]) or []


def _save(items: list[dict[str, Any]]) -> None:
    jsonstore.write_json(paths.pending_file(), items)


def add(channel: str, kind: str, desc: str, kwargs: dict[str, Any]) -> str:
    """Поставить действие в очередь на подтверждение. Возвращает короткий id.

    Дедуп: если идентичное действие (та же сущность/тип/параметры) уже в очереди —
    не добавляем второе (иначе агент мог бы создать две кнопки на один и тот же пост).
    """
    items = _load()
    for i in items:
        if i.get("channel") == channel and i.get("kind") == kind and i.get("kwargs") == kwargs:
            return i["id"]  # уже ждёт подтверждения — не дублируем
    pid = _secrets.token_hex(3)
    items.append({"id": pid, "channel": channel, "kind": kind, "desc": desc, "kwargs": kwargs})
    _save(items)
    log.info("pending +%s: %s (%s)", pid, kind, desc)
    return pid


def list_pending(channel: str) -> list[dict[str, Any]]:
    return [i for i in _load() if i.get("channel") == channel]


def pop_by_id(pid: str) -> dict[str, Any] | None:
    items = _load()
    found: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for i in items:
        if i.get("id") == pid and found is None:
            found = i
        else:
            rest.append(i)
    if found is not None:
        _save(rest)
    return found


async def execute(record: dict[str, Any]) -> dict[str, Any]:
    """Реально выполнить подтверждённое действие (вызывается ботом по нажатию ✅)."""
    kind = record["kind"]
    kw = record["kwargs"]
    if kind == "post":
        from .telegram import posting

        return await posting.post_now(kw["channel"], kw["draft"])
    if kind == "schedule":
        from .telegram import posting

        return await posting.schedule_post(kw["channel"], kw["draft"], kw["at"])
    if kind == "edit":
        from .telegram import posting

        return await posting.edit_message(kw["channel"], kw["msg_id"], kw["text"])
    if kind == "delete":
        from .telegram import posting

        return await posting.delete_message(kw["channel"], kw["msg_id"])
    if kind == "poll":
        from .telegram import polls

        return await polls.create_poll(
            kw["channel"], kw["question"], kw["options"],
            quiz=kw.get("quiz", False), correct=kw.get("correct"), multiple=kw.get("multiple", False),
        )
    if kind == "reply":
        from .telegram import groups

        return await groups.reply(kw["channel"], kw["to"], body=kw.get("text"), draft_path=kw.get("draft"))
    raise ValueError(f"неизвестный тип действия: {kind}")
