"""Tier-2 аналитика (только крупные каналы) с мягким fallback в Tier-1.

``GetBroadcastStatsRequest`` доступен лишь каналам выше внутреннего порога Telegram.
На малых — ``ChatAdminRequiredError`` / stats unavailable. Мы пробуем один раз,
ловим ошибку, запоминаем результат в ``config.yaml`` (``broadcast_stats``), чтобы
не дёргать API каждый раз.
"""

from __future__ import annotations

from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    ChatAdminRequiredError,
    RPCError,
)
from telethon.tl.functions.stats import (
    GetBroadcastStatsRequest,
    GetMessageStatsRequest,
)

from ..config import ChannelConfig
from ..util.logging import get_logger
from ..util.ratelimit import with_floodwait
from .client import resolve_entity

log = get_logger("stats_api")


async def probe_broadcast_stats(client: TelegramClient, entity: Any) -> dict | None:
    """Попробовать получить broadcast-статистику. None == недоступно (мягко падаем в Tier-1)."""
    try:
        stats = await with_floodwait(client, GetBroadcastStatsRequest(channel=entity))
    except ChatAdminRequiredError:
        log.info("Tier-2: нужны админ-права — broadcast stats недоступны")
        return None
    except RPCError as e:
        # частый случай на малых каналах: STATS_MIGRATE / broadcast stats unavailable
        log.info("Tier-2 недоступны: %s", e)
        return None

    # вытаскиваем самые полезные агрегаты; остальное доступно в сыром объекте при желании
    def _cur(period: Any) -> float | None:
        # StatsAbsValueAndPrev: текущее абсолютное значение
        return float(getattr(period, "current", 0)) if period is not None else None

    def _pct(value: Any) -> float | None:
        # StatsPercentValue: доля part/total → проценты (у неё нет .current!)
        if value is None:
            return None
        total = float(getattr(value, "total", 0) or 0)
        part = float(getattr(value, "part", 0) or 0)
        return (part / total * 100) if total else None

    return {
        "followers_current": _cur(getattr(stats, "followers", None)),
        "views_per_post": _cur(getattr(stats, "views_per_post", None)),
        "shares_per_post": _cur(getattr(stats, "shares_per_post", None)),
        "enabled_notifications_pct": _pct(getattr(stats, "enabled_notifications", None)),
    }


async def message_stats(client: TelegramClient, entity: Any, msg_id: int) -> bool:
    """Детальная статистика одного поста. True == доступно (объект получен)."""
    try:
        await with_floodwait(client, GetMessageStatsRequest(channel=entity, msg_id=msg_id))
        return True
    except (ChatAdminRequiredError, RPCError) as e:
        log.info("Message stats недоступны для %s: %s", msg_id, e)
        return False


async def resolve_tier(
    client: TelegramClient, cfg: ChannelConfig, tier: str = "auto"
) -> tuple[str, dict | None]:
    """Определить доступный тир аналитики и (опц.) broadcast-данные.

    tier:
      - ``basic``     — не лезть в сеть, только Tier-1.
      - ``broadcast`` — требовать Tier-2 (если нет — вернуть unavailable).
      - ``auto``      — если в config уже знаем → уважаем; иначе пробуем один раз и запоминаем.

    Возвращает (broadcast_stats_status, broadcast_data|None).
    """
    if tier == "basic":
        return cfg.broadcast_stats, None

    if tier == "auto" and cfg.broadcast_stats in ("available", "unavailable"):
        if cfg.broadcast_stats == "unavailable":
            return "unavailable", None
        # available — всё равно тянем свежие данные
    entity = await resolve_entity(client, cfg.handle)
    data = await probe_broadcast_stats(client, entity)
    status = "available" if data is not None else "unavailable"

    if status != cfg.broadcast_stats:
        cfg.broadcast_stats = status
        cfg.save()
        log.info("Запомнил broadcast_stats=%s для '%s'", status, cfg.name)
    return status, data
