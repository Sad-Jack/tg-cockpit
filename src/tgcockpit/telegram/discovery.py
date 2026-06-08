"""Авто-обнаружение каналов/групп, куда добавлен наш БОТ как администратор.

Логика под ожидание пользователя: «покажи каналы/группы, где бот — админ».
Перебираем диалоги user-сессии (``iter_dialogs``); для каждого канала/группы, где сам
пользователь админ/создатель (иначе постить через user-сессию нельзя), проверяем, что
БОТ там админ (``get_permissions(entity, bot_id)``). Пользователь выбирает из списка.

``rights_missing`` — чистая функция, тестируется без сети.
"""

from __future__ import annotations

import re
from typing import Any

from ..config import ChannelConfig, load_secrets
from ..storage import workspace
from ..util.logging import get_logger
from .client import connected, resolve_entity

log = get_logger("discovery")

# права пользователя, без которых не сможем вести сущность через user-сессию
NEEDED_RIGHTS = {
    "channel": ("post_messages", "edit_messages", "delete_messages"),
    "supergroup": ("delete_messages",),
    "group": ("delete_messages",),
}


def _kind(entity: Any) -> str:
    """Тип сущности по duck-typed атрибутам (работает на Telethon-типах и на моках)."""
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    return "group"


def _handle(entity: Any) -> str:
    uname = getattr(entity, "username", None)
    return f"@{uname}" if uname else str(getattr(entity, "id", ""))


def rights_missing(kind: str, is_creator: bool, admin_rights: Any) -> list[str]:
    """Каких прав не хватает для ведения сущности (пусто = всё ок). Создатель — всё имеет."""
    if is_creator:
        return []
    needed = NEEDED_RIGHTS.get(kind, ())
    if admin_rights is None:
        return list(needed)
    return [r for r in needed if not getattr(admin_rights, r, False)]


def _bot_id() -> int | None:
    """id бота из bot_token (часть до ':'). None — если токена нет."""
    try:
        token = load_secrets().bot_token
    except Exception:  # noqa: BLE001
        return None
    if not token or ":" not in token:
        return None
    try:
        return int(token.split(":")[0])
    except ValueError:
        return None


def suggest_name(handle: str, title: str = "") -> str:
    """Имя воркспейса (папки) из @handle/заголовка, усечённое по байтам под callback_data."""
    if handle.startswith("@"):
        base = handle[1:]
    elif handle.lstrip("-").isdigit():
        base = ""  # числовой id → имя из заголовка
    else:
        base = handle
    if not base:
        base = re.sub(r"[^\w]+", "-", (title or "entity").lower(), flags=re.UNICODE).strip("-")
    base = re.sub(r"[^\w\-]+", "", base, flags=re.UNICODE)
    while len(base.encode("utf-8")) > 50:  # "sw:<name>" должно влезть в 64 байта
        base = base[:-1]
    return base or "entity"


def unique_name(name: str) -> str:
    existing = set(workspace.list_channels())
    if name not in existing:
        return name
    i = 2
    while f"{name}-{i}" in existing:
        i += 1
    return f"{name}-{i}"


def existing_handles() -> set[str]:
    """Хэндлы уже добавленных сущностей (нормализованные) — чтобы не предлагать повторно."""
    out: set[str] = set()
    for name in workspace.list_channels():
        try:
            out.add(ChannelConfig.load(name).handle.lstrip("@").lower())
        except Exception:  # noqa: BLE001
            continue
    return out


async def list_admin_entities(limit: int = 500) -> list[dict[str, Any]]:
    """Каналы/группы, где БОТ добавлен админом (и пользователь может постить).

    Уже добавленные исключаются. Сортировка: сперва с полными правами пользователя.
    """
    already = existing_handles()
    bot_id = _bot_id()
    found: list[dict[str, Any]] = []
    async with connected() as client:
        async for dialog in client.iter_dialogs(limit=limit):
            e = dialog.entity
            if getattr(e, "title", None) is None:
                continue  # личные чаты (User) пропускаем
            user_creator = bool(getattr(e, "creator", False))
            user_ar = getattr(e, "admin_rights", None)
            if not user_creator and user_ar is None:
                continue  # пользователь не админ — через user-сессию не поведём

            # ключевой фильтр: бот добавлен сюда админом
            if bot_id is not None:
                try:
                    perms = await client.get_permissions(e, bot_id)
                    if not (perms.is_admin or perms.is_creator):
                        continue
                except Exception:  # noqa: BLE001 — бот не участник/не виден → пропуск
                    continue

            handle = _handle(e)
            if handle.lstrip("@").lower() in already:
                continue
            kind = _kind(e)
            missing = rights_missing(kind, user_creator, user_ar)
            found.append({
                "handle": handle,
                "title": getattr(e, "title", None) or handle,
                "kind": kind,
                "rights_ok": not missing,
                "missing": missing,
            })
    found.sort(key=lambda q: (not q["rights_ok"], q["title"].lower()))
    log.info("discovery: бот-админ сущностей (не добавленных): %s", len(found))
    return found


async def check_entities(names: list[str]) -> dict[str, dict[str, Any]]:
    """Проверить актуальность уже добавленных сущностей (резолвятся ли сейчас)."""
    res: dict[str, dict[str, Any]] = {}
    if not names:
        return res
    async with connected() as client:
        for name in names:
            try:
                cfg = ChannelConfig.load(name)
                ent = await resolve_entity(client, cfg.handle)
                res[name] = {"ok": True, "kind": _kind(ent), "title": getattr(ent, "title", "")}
            except Exception as e:  # noqa: BLE001
                res[name] = {"ok": False, "reason": str(e)}
    return res
