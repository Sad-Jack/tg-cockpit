"""Бэкофф на FloodWait + пейсинг. Оборачивает любой Telethon-корутинный вызов.

Telethon при превышении лимитов бросает ``FloodWaitError(seconds=...)``. Правильная
реакция — поспать ровно столько (плюс небольшой джиттер) и повторить. Никаких
тайт-лупов: на больших паузах честно ждём, на маленьких — не спамим повторами.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telethon.errors import FloodWaitError

from .logging import get_logger

log = get_logger("ratelimit")

T = TypeVar("T")

# Если Telegram просит ждать дольше — не блокируемся молча, а пробрасываем наверх:
# скорее всего это сигнал «слишком агрессивно», лучше остановиться и сказать человеку.
MAX_AUTO_WAIT_SECONDS = 600  # 10 минут


async def with_floodwait(
    func: Callable[..., Awaitable[T]],
    *args: object,
    retries: int = 5,
    max_auto_wait: int = MAX_AUTO_WAIT_SECONDS,
    **kwargs: object,
) -> T:
    """Вызвать ``func(*args, **kwargs)`` с авто-ретраем на FloodWait.

    Пример::

        msgs = await with_floodwait(client.get_messages, entity, limit=100)
    """
    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            attempt += 1
            wait = int(e.seconds)
            if wait > max_auto_wait:
                log.error("FloodWait %ss > лимит %ss — пробрасываю наверх", wait, max_auto_wait)
                raise
            if attempt > retries:
                log.error("FloodWait: исчерпаны %s ретраев", retries)
                raise
            # небольшой детерминированный джиттер от номера попытки (без random — воспроизводимо)
            jitter = min(5, attempt)
            sleep_for = wait + jitter
            log.warning(
                "FloodWait %ss (попытка %s/%s) — сплю %ss", wait, attempt, retries, sleep_for
            )
            await asyncio.sleep(sleep_for)


async def pace(seconds: float) -> None:
    """Мягкая пауза между однотипными вызовами (пагинация фетча и т.п.)."""
    if seconds > 0:
        await asyncio.sleep(seconds)
