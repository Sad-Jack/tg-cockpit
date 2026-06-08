"""Чтение сообщений группы и ответы на них — с учётом исключений.

Бот «просматривает» чат через user-сессию (читает как сам аккаунт). Исключения
(``config.exclusions``) позволяют не учитывать сообщения определённых пользователей,
по ключевым словам или конкретные id — чтобы агент не реагировал на ботов, спам, себя.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon import TelegramClient

from ..config import ChannelConfig, Exclusions
from ..storage import drafts as drafts_mod
from ..storage import workspace
from ..storage.drafts import Draft
from ..util.formatting import PARSE_MODE
from ..util.logging import get_logger
from ..util.ratelimit import with_floodwait
from .client import connected, resolve_entity

log = get_logger("groups")


def is_excluded(record: dict[str, Any], exclusions: Exclusions) -> bool:
    """Пройдёт ли сообщение фильтр исключений (True = исключить)."""
    if record["id"] in exclusions.message_ids:
        return True
    text = (record.get("text") or "").lower()
    if any(kw.lower() in text for kw in exclusions.keywords if kw):
        return True
    # users: совпадение по числовому id или по @username (с/без @)
    sender_id = str(record.get("sender_id") or "")
    username = (record.get("username") or "").lstrip("@").lower()
    for u in exclusions.users:
        uu = u.lstrip("@").lower()
        if uu and (uu == sender_id or uu == username):
            return True
    return False


def _record(msg: Any) -> dict[str, Any]:
    sender = getattr(msg, "sender", None)
    date: datetime | None = getattr(msg, "date", None)
    return {
        "id": int(msg.id),
        "sender_id": int(getattr(msg, "sender_id", 0) or 0),
        "username": getattr(sender, "username", None) if sender else None,
        "date": date.isoformat() if date else None,
        "text": getattr(msg, "message", "") or "",
        "reply_to": getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None),
    }


async def read_recent(
    channel: str, limit: int = 50, apply_exclusions: bool = True
) -> list[dict[str, Any]]:
    """Прочитать последние ``limit`` сообщений группы, применив исключения."""
    cfg = ChannelConfig.load(channel)
    out: list[dict[str, Any]] = []
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        async for msg in client.iter_messages(entity, limit=limit):
            if getattr(msg, "id", None) is None:
                continue
            rec = _record(msg)
            if apply_exclusions and is_excluded(rec, cfg.exclusions):
                continue
            out.append(rec)
    log.info("read '%s': %s сообщений (после исключений)", channel, len(out))
    return out


async def reply(
    channel: str,
    to_msg_id: int,
    body: str | None = None,
    draft_path: str | None = None,
) -> dict[str, Any]:
    """Ответить на сообщение группы (reply_to). Текст — напрямую или из черновика (HTML)."""
    if not body and not draft_path:
        raise ValueError("нужен либо body, либо draft_path")
    cfg = workspace.require_studied(channel)  # гард: отвечать можно только после изучения

    media: Any = None
    if draft_path:
        d = drafts_mod.load(drafts_mod.resolve_in_channel(channel, draft_path))
        body = d.body
        media = _draft_media(d)

    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        kwargs: dict[str, Any] = {"message": body, "reply_to": int(to_msg_id), "parse_mode": PARSE_MODE}
        if media:
            kwargs["file"] = media
        msg = await with_floodwait(client.send_message, entity, **kwargs)
    log.info("reply '%s': to=%s msg_id=%s", channel, to_msg_id, msg.id)
    return {"msg_id": int(msg.id), "reply_to": int(to_msg_id)}


def _draft_media(d: Draft) -> Any:
    from .posting import _resolve_media

    files = _resolve_media(d.media)
    if not files:
        return None
    return files if len(files) > 1 else files[0]
