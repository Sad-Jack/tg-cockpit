"""Постинг и серверное планирование — линчпин проекта.

Ключевое: ``schedule=`` кладёт пост в **серверную** очередь Telegram → он опубликуется,
даже если Mac выключен. Лимит — 100 на чат, до года вперёд.

Реконсиляция: истина — серверная очередь (``GetScheduledMessagesRequest``), а НЕ кэш
истории. ``iter_messages`` отложенные не показывает — частый источник двойного постинга.
ID отложенного поста пишем в frontmatter черновика → можем отменить/перепланировать без дублей.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.functions.messages import (
    DeleteScheduledMessagesRequest,
    GetScheduledHistoryRequest,
)

from .. import paths
from ..config import ChannelConfig
from ..storage import drafts as drafts_mod
from ..storage import workspace
from ..storage.drafts import Draft
from ..util.formatting import PARSE_MODE
from ..util.logging import get_logger
from ..util.ratelimit import with_floodwait
from ..util.timefmt import is_future, parse_when
from .client import connected, resolve_entity

log = get_logger("posting")

SCHEDULE_LIMIT = 100  # серверный лимит Telegram на отложенные сообщения в одном чате


def _resolve_media(media: list[str]) -> list[Path]:
    """Резолв путей медиа в абсолютные. Относительные пути обязаны оставаться внутри репо.

    Защита от traversal: относительный путь с ``..``, уводящий за пределы репо
    (напр. ``../../etc/passwd``), отвергается. Абсолютные пути — явный выбор человека,
    допускаются как есть (картинка может лежать в ~/Downloads).
    """
    root = paths.repo_root().resolve()
    out: list[Path] = []
    for m in media:
        p = Path(m)
        if p.is_absolute():
            p = p.resolve()
        else:
            p = (root / m).resolve()
            if not p.is_relative_to(root):
                raise ValueError(f"путь медиа вне репозитория (traversal?): {m}")
        if not p.exists():
            raise FileNotFoundError(f"медиа-файл не найден: {p}")
        out.append(p)
    return out


async def _send(client: TelegramClient, entity: Any, draft: Draft, schedule: Any | None):
    """Низкоуровневая отправка (сейчас или в очередь). Альбом — одним вызовом file=[...]"""
    files = _resolve_media(draft.media)
    kwargs: dict[str, Any] = {"message": draft.body, "parse_mode": PARSE_MODE}
    if files:
        kwargs["file"] = files if len(files) > 1 else files[0]
    if schedule is not None:
        kwargs["schedule"] = schedule
    return await with_floodwait(client.send_message, entity, **kwargs)


async def edit_message(channel: str, msg_id: int, new_body: str) -> dict[str, Any]:
    """Отредактировать опубликованное сообщение (текст/подпись) в HTML.

    Опросы текстом не редактируются (Telegram) — только обычные сообщения/посты.
    """
    cfg = ChannelConfig.load(channel)
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        msg = await with_floodwait(
            client.edit_message, entity, int(msg_id), new_body, parse_mode=PARSE_MODE
        )
    log.info("edit '%s': msg_id=%s", channel, msg_id)
    return {"msg_id": int(getattr(msg, "id", msg_id)), "status": "edited"}


async def delete_message(channel: str, msg_id: int, revoke: bool = True) -> dict[str, Any]:
    """Удалить сообщение по id (revoke=True — у всех). Чистит отметку в связанном черновике."""
    cfg = ChannelConfig.load(channel)
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        await with_floodwait(client.delete_messages, entity, [int(msg_id)], revoke=revoke)
    for draft in drafts_mod.list_drafts(channel):
        if draft.scheduled_msg_id == int(msg_id):
            draft.scheduled_msg_id = None
            draft.status = "draft"
            drafts_mod.save(draft)
            break
    log.info("delete '%s': msg_id=%s", channel, msg_id)
    return {"deleted": int(msg_id)}


async def list_scheduled(channel: str) -> list[dict[str, Any]]:
    """Прочитать серверную очередь отложенных постов (источник истины)."""
    cfg = ChannelConfig.load(channel)
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        res = await with_floodwait(client, GetScheduledHistoryRequest(peer=entity, hash=0))
        out = []
        for m in getattr(res, "messages", []):
            sched = getattr(m, "date", None)
            out.append(
                {
                    "id": int(m.id),
                    "schedule_at": sched.isoformat() if sched else None,
                    "preview": (getattr(m, "message", "") or "")[:80],
                }
            )
        return out


async def scheduled_count(channel: str) -> int:
    return len(await list_scheduled(channel))


async def post_now(channel: str, draft_path: str) -> dict[str, Any]:
    """Опубликовать черновик немедленно. Обновляет статус черновика на posted."""
    cfg = workspace.require_studied(channel)  # гард: постить можно только после изучения
    draft = drafts_mod.load(drafts_mod.resolve_in_channel(channel, draft_path))
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        msg = await _send(client, entity, draft, schedule=None)
    draft.status = "posted"
    draft.scheduled_msg_id = None
    drafts_mod.save(draft)
    log.info("post-now '%s': msg_id=%s", channel, msg.id)
    return {"msg_id": int(msg.id), "status": "posted"}


async def schedule_post(channel: str, draft_path: str, when: str) -> dict[str, Any]:
    """Поставить черновик в серверную очередь на время ``when`` (в таймзоне канала).

    Реконсиляция: читаем очередь; если у черновика уже есть живой scheduled_msg_id —
    отказываемся (во избежание дубля), предлагаем сначала cancel/reschedule. Проверяем лимит 100.
    """
    cfg = workspace.require_studied(channel)  # гард: планировать можно только после изучения
    draft = drafts_mod.load(drafts_mod.resolve_in_channel(channel, draft_path))

    when_dt = parse_when(when, cfg.timezone)
    if not is_future(when_dt, cfg.timezone):
        raise ValueError(f"время {when!r} не в будущем (нужен запас ≥60с) в tz={cfg.timezone}")

    queue = await list_scheduled(channel)
    queue_ids = {q["id"] for q in queue}

    # анти-дубль: черновик уже стоит в очереди
    if draft.scheduled_msg_id and draft.scheduled_msg_id in queue_ids:
        raise ValueError(
            f"черновик уже запланирован (msg_id={draft.scheduled_msg_id}). "
            f"Сначала отмени: tgcockpit scheduled cancel --channel {channel} "
            f"--id {draft.scheduled_msg_id}"
        )

    if len(queue) >= SCHEDULE_LIMIT:
        raise ValueError(
            f"очередь заполнена ({len(queue)}/{SCHEDULE_LIMIT}). Telegram не примет ещё один."
        )

    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        msg = await _send(client, entity, draft, schedule=when_dt)

    draft.status = "scheduled"
    draft.schedule_at = when_dt.isoformat()
    draft.scheduled_msg_id = int(msg.id)
    drafts_mod.save(draft)
    log.info("schedule '%s': msg_id=%s at %s", channel, msg.id, when_dt.isoformat())
    return {"msg_id": int(msg.id), "schedule_at": when_dt.isoformat(), "queue_size": len(queue) + 1}


async def cancel_scheduled(channel: str, msg_id: int) -> dict[str, Any]:
    """Отменить отложенный пост по id. Чистит scheduled_msg_id в связанном черновике."""
    cfg = ChannelConfig.load(channel)
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        await with_floodwait(
            client, DeleteScheduledMessagesRequest(peer=entity, id=[int(msg_id)])
        )

    # реконсиляция frontmatter: найти черновик с этим id и снять отметку
    for draft in drafts_mod.list_drafts(channel):
        if draft.scheduled_msg_id == int(msg_id):
            draft.scheduled_msg_id = None
            draft.status = "draft"
            drafts_mod.save(draft)
            break
    log.info("cancel '%s': msg_id=%s", channel, msg_id)
    return {"cancelled": int(msg_id)}
