"""Чтение истории канала + извлечение Tier-1 метрик по каждому посту.

Tier-1 (всегда доступно, без особых прав сверх чтения канала):
``views``, ``forwards``, ``reactions``, число комментариев (``replies``), альбомы
(``grouped_id``). Инкрементальный рефетч — по сохранённому ``max(id)``.

``extract_record`` — чистая функция (мокается в тестах), сетевой код — в ``fetch_history``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import InputMessagesFilterPinned

from ..storage import jsonstore, workspace
from ..util.logging import get_logger
from ..util.ratelimit import pace, with_floodwait
from .client import connected, entity_kind, resolve_entity

log = get_logger("history")


def _media_kind(msg: Any) -> str:
    """Грубая классификация типа поста для аналитики по медиа."""
    if getattr(msg, "poll", None):
        return "poll"
    if getattr(msg, "photo", None):
        return "photo"
    if getattr(msg, "video", None) or getattr(msg, "video_note", None):
        return "video"
    if getattr(msg, "voice", None) or getattr(msg, "audio", None):
        return "audio"
    if getattr(msg, "document", None):
        return "document"
    if getattr(msg, "web_preview", None) or getattr(msg, "webpage", None):
        return "link"
    if getattr(msg, "message", None):
        return "text"
    return "other"


def _reactions(msg: Any) -> tuple[int, dict[str, int]]:
    """Сумма реакций и разбивка по эмодзи."""
    reactions = getattr(msg, "reactions", None)
    if not reactions or not getattr(reactions, "results", None):
        return 0, {}
    total = 0
    by_emoji: dict[str, int] = {}
    for rc in reactions.results:
        count = int(getattr(rc, "count", 0) or 0)
        total += count
        reaction = getattr(rc, "reaction", None)
        key = (
            getattr(reaction, "emoticon", None)
            or (f"custom:{getattr(reaction, 'document_id', '?')}" if reaction else "?")
        )
        by_emoji[str(key)] = by_emoji.get(str(key), 0) + count
    return total, by_emoji


def extract_record(msg: Any) -> dict[str, Any]:
    """Превратить Telethon-сообщение в плоскую запись для кэша. Чистая функция."""
    total_reactions, by_emoji = _reactions(msg)
    replies_obj = getattr(msg, "replies", None)
    text = getattr(msg, "message", "") or ""
    date: datetime | None = getattr(msg, "date", None)
    return {
        "id": int(msg.id),
        "date": date.isoformat() if date else None,
        "views": int(getattr(msg, "views", 0) or 0),
        "forwards": int(getattr(msg, "forwards", 0) or 0),
        "reactions": total_reactions,
        "reactions_by_emoji": by_emoji,
        "replies": int(getattr(replies_obj, "replies", 0) or 0) if replies_obj else 0,
        "grouped_id": int(msg.grouped_id) if getattr(msg, "grouped_id", None) else None,
        "media_kind": _media_kind(msg),
        "char_count": len(text),
        "text": text,  # полный текст (для Obsidian-хранилища); берём ТОЛЬКО текст
        "text_preview": text[:140],
    }


async def _subscribers(client: TelegramClient, entity: Any) -> int | None:
    """Число подписчиков канала через GetFullChannelRequest (для ERR)."""
    try:
        full = await with_floodwait(client, GetFullChannelRequest(channel=entity))
        return int(getattr(full.full_chat, "participants_count", 0)) or None
    except Exception as e:  # noqa: BLE001 — не критично, ERR просто не посчитается
        log.warning("Не удалось получить число подписчиков: %s", e)
        return None


async def _refresh_window(
    client: TelegramClient, entity: Any, records: dict[str, dict[str, Any]], window: int
) -> tuple[int, int]:
    """Освежить последние ``window`` постов + закреплённые: ловит правки/удаления без полного прохода.

    Возвращает (обновлено, удалено). Удаления детектятся только в пределах окна последних
    постов: кэшированный id внутри диапазона окна, не пришедший от Telegram → удалён.
    """
    refreshed = 0
    deleted = 0

    # 1) последние N постов
    recent_ids: set[int] = set()
    async for msg in client.iter_messages(entity, limit=window):
        if getattr(msg, "id", None) is None:
            continue
        recent_ids.add(int(msg.id))
        records[str(msg.id)] = extract_record(msg)
        refreshed += 1
    if recent_ids:
        floor = min(recent_ids)
        for key in list(records):
            kid = int(key)
            if kid >= floor and kid not in recent_ids:
                del records[key]  # был в окне, но Telegram его не вернул → удалён
                deleted += 1

    # 2) закреплённые (могут быть старее окна) — освежаем содержимое
    try:
        async for msg in client.iter_messages(entity, filter=InputMessagesFilterPinned()):
            if getattr(msg, "id", None) is None:
                continue
            records[str(msg.id)] = extract_record(msg)
            refreshed += 1
    except Exception as e:  # noqa: BLE001 — закреп не критичен
        log.info("refresh pinned пропущен: %s", e)

    return refreshed, deleted


async def fetch_history(
    channel: str,
    limit: int | None = None,
    since: datetime | None = None,
    full: bool = False,
    pace_seconds: float = 0.0,
    recent_window: int = 100,
) -> dict[str, Any]:
    """Скачать историю канала в ``data/history.json`` (инкрементально по умолчанию).

    Возвращает сводку: сколько новых/всего записей и snapshot канала.
    """
    cfg = workspace.require_channel(channel)
    hist_path = workspace.history_file(channel)

    existing_raw = jsonstore.read_json(hist_path, default={}) or {}
    records: dict[str, dict[str, Any]] = dict(existing_raw.get("posts", {}))
    prev_max = max((int(k) for k in records), default=0)

    # инкрементально: тянем только посты новее сохранённого максимума
    min_id = 0 if (full or not records) else prev_max

    new_count = 0
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        # авто-определение типа сущности (channel/group/supergroup) при первом фетче
        detected = entity_kind(entity)
        if detected != cfg.kind:
            cfg.kind = detected
            cfg.save()
        kwargs: dict[str, Any] = {}
        if limit:
            kwargs["limit"] = limit
        if since:
            kwargs["offset_date"] = since
        if min_id:
            kwargs["min_id"] = min_id

        async for msg in client.iter_messages(entity, **kwargs):
            if getattr(msg, "id", None) is None:
                continue
            rec = extract_record(msg)
            key = str(rec["id"])
            if key not in records:
                new_count += 1
            records[key] = rec  # перезапись освежает метрики при full-рефетче
            if pace_seconds:
                await pace(pace_seconds)

        # лёгкая актуализация (только при инкременте): последние N постов + закреплённые,
        # чтобы ловить правки/удаления свежих постов без полного прохода по всему каналу
        refreshed = deleted = 0
        if not full:
            refreshed, deleted = await _refresh_window(client, entity, records, recent_window)

        subscribers = await _subscribers(client, entity)

    snapshot = {
        "channel": channel,
        "handle": cfg.handle,
        "subscribers": subscribers,
        "total_posts": len(records),
        "max_id": max((int(k) for k in records), default=0),
        "fetched_mode": "full" if min_id == 0 else "incremental",
    }
    payload = {"snapshot": snapshot, "posts": records}
    jsonstore.write_json(hist_path, payload)

    log.info(
        "fetch-history '%s': +%s новых, ~%s освежено, -%s удалено, всего %s",
        channel, new_count, refreshed, deleted, len(records)
    )
    return {
        "new": new_count,
        "refreshed": refreshed,
        "deleted": deleted,
        "total": len(records),
        "snapshot": snapshot,
    }
