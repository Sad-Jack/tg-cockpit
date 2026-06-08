"""Сериализация доступа к Telethon-сессии: параллельные connected() не пересекаются.

Регресс на "database is locked" — когда несколько кнопок ✅ нажаты разом, хэндлеры
бота лезли в один SQLite-файл сессии одновременно. Теперь весь connected() под локом.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tgcockpit.telegram import client as cl


class _FakeClient:
    """Считает, сколько клиентов держат «сессию» одновременно (должен быть максимум 1)."""

    def __init__(self, state: dict) -> None:
        self.state = state

    async def connect(self) -> None:
        self.state["active"] += 1
        self.state["max"] = max(self.state["max"], self.state["active"])
        await asyncio.sleep(0.005)  # удерживаем «сессию», чтобы поймать пересечение

    async def is_user_authorized(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self.state["active"] -= 1


async def test_connected_serializes_concurrent_sessions(monkeypatch):
    state = {"active": 0, "max": 0}
    monkeypatch.setattr(cl, "load_secrets", lambda: SimpleNamespace(account="acc"))
    monkeypatch.setattr(cl, "build_client", lambda secrets=None: _FakeClient(state))
    monkeypatch.setattr(cl, "_harden_session_perms", lambda account: None)

    async def use_session() -> None:
        async with cl.connected():
            await asyncio.sleep(0.005)  # работа под сессией

    # три параллельных потребителя сессии — без лока было бы max == 3 и "database is locked"
    await asyncio.gather(*(use_session() for _ in range(3)))

    assert state["max"] == 1, f"сессии пересеклись (max={state['max']}) — лок не сработал"
    assert state["active"] == 0  # все корректно отключились


async def test_session_lock_is_per_loop():
    """В одном цикле — один и тот же лок (иначе сериализация не работает)."""
    a = cl._session_lock()
    b = cl._session_lock()
    assert a is b
