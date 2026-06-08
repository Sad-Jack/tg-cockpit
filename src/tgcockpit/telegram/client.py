"""Фабрика Telethon-клиента + сессия + резолв entity.

Единственная точка создания клиента. Сессия лежит в ``secrets/sessions/<account>.session``
(SQLite, полный доступ к аккаунту → chmod 600). Любой сетевой код берёт клиент отсюда.
"""

from __future__ import annotations

import asyncio
import os
import stat
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

from .. import paths
from ..config import Secrets, load_secrets
from ..util.logging import get_logger
from ..util.ratelimit import with_floodwait

log = get_logger("client")

# Сессия Telethon — это один SQLite-файл (.session). Параллельные клиенты на нём дают
# "database is locked" (напр. пользователь жмёт несколько кнопок ✅ подряд → хэндлеры
# бота лезут в сессию одновременно). Поэтому весь сеанс connected() идёт под локом —
# строго по одному за раз. Лок — на каждый event loop свой (WeakKeyDictionary), чтобы не
# падать между разными asyncio.run (CLI/тесты создают новый цикл на каждый вызов).
_session_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _session_lock() -> asyncio.Lock:
    """Лок сериализации доступа к SQLite-сессии, привязанный к текущему event loop."""
    loop = asyncio.get_running_loop()
    lock = _session_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[loop] = lock
    return lock


def build_client(secrets: Secrets | None = None) -> TelegramClient:
    """Собрать (но не подключать) Telethon-клиент из секретов."""
    secrets = secrets or load_secrets()
    sessions = paths.sessions_dir()
    sessions.mkdir(parents=True, exist_ok=True)
    # каталог секретов — приватный (0o700) независимо от umask: и сессии, и .env эквивалентны паролю
    for d in (paths.secrets_dir(), sessions):
        try:
            os.chmod(d, stat.S_IRWXU)  # 0o700
        except OSError as e:  # pragma: no cover - зависит от ФС
            log.warning("Не смог выставить chmod 700 на %s: %s", d, e)
    session_path = str(paths.session_file(secrets.account))
    return TelegramClient(session_path, secrets.api_id, secrets.api_hash)


def _harden_session_perms(account: str) -> None:
    """chmod 600 на файл сессии — он эквивалентен паролю от аккаунта."""
    f = paths.session_file(account).with_suffix(".session")
    if f.exists():
        try:
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except OSError as e:  # pragma: no cover - зависит от ФС
            log.warning("Не смог выставить chmod 600 на %s: %s", f, e)


@asynccontextmanager
async def connected(secrets: Secrets | None = None) -> AsyncIterator[TelegramClient]:
    """Контекст: подключённый клиент. НЕ запускает интерактивный логин.

    Если сессии нет/она невалидна — бросает понятную ошибку (запусти ``auth``).
    """
    secrets = secrets or load_secrets()
    # под локом: пока один клиент держит сессию, второй ждёт — без "database is locked"
    async with _session_lock():
        client = build_client(secrets)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Нет валидной сессии. Сначала выполни: tgcockpit auth"
                )
            _harden_session_perms(secrets.account)
            yield client
        finally:
            await client.disconnect()


async def interactive_login(secrets: Secrets | None = None) -> User:
    """Интерактивный логин (телефон → код → 2FA). Используется командой ``auth``.

    Возвращает залогиненного пользователя. Сессия пишется на диск Telethon-ом.
    """
    secrets = secrets or load_secrets()
    client = build_client(secrets)
    # client.start() сам спросит телефон/код/пароль через input() при необходимости
    await client.start()
    me = await client.get_me()
    _harden_session_perms(secrets.account)
    await client.disconnect()
    return me  # type: ignore[return-value]


async def resolve_entity(client: TelegramClient, handle: str):
    """Резолв @username или числового id в entity, с обёрткой FloodWait."""
    target: str | int = handle
    if isinstance(handle, str) and handle.lstrip("-").isdigit():
        target = int(handle)
    return await with_floodwait(client.get_entity, target)


def entity_kind(entity: object) -> str:
    """Классифицировать entity: ``channel`` | ``supergroup`` | ``group``.

    Telethon: широковещательный канал и супергруппа — оба ``Channel``, различаются
    флагом ``megagroup``; старые маленькие группы — ``Chat``.
    """
    if isinstance(entity, Channel):
        return "supergroup" if getattr(entity, "megagroup", False) else "channel"
    if isinstance(entity, Chat):
        return "group"
    return "channel"


async def whoami(secrets: Secrets | None = None) -> User:
    """Вернуть текущего залогиненного пользователя (проверка сессии)."""
    async with connected(secrets) as client:
        return await client.get_me()  # type: ignore[return-value]
