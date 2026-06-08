"""Тесты лёгкой актуализации истории: последние N постов + закреплённые (правки/удаления)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tgcockpit.telegram import history


class _FakeClient:
    """Мок Telethon: iter_messages(limit=...) → recent; iter_messages(filter=...) → pinned."""

    def __init__(self, recent, pinned):
        self.recent = recent
        self.pinned = pinned

    def iter_messages(self, entity, limit=None, filter=None, **kw):
        msgs = self.pinned if filter is not None else self.recent

        async def _gen():
            for m in msgs:
                yield m

        return _gen()


def _msg(mid, text):
    return SimpleNamespace(id=mid, message=text, date=datetime(2026, 5, 1, 9, 0))


async def test_refresh_window_edit_delete_pinned():
    records = {
        "100": {"id": 100, "text": "старый пост ВНЕ окна"},   # ниже floor — не трогаем
        "200": {"id": 200, "text": "старый текст"},            # в окне — отредактирован
        "201": {"id": 201, "text": "будет удалён"},            # в окне, не вернулся — удалён
    }
    recent = [_msg(202, "новый пост"), _msg(200, "ОТРЕДАКТИРОВАНО")]
    pinned = [_msg(5, "закреп обновлён")]
    client = _FakeClient(recent, pinned)

    refreshed, deleted = await history._refresh_window(client, "entity", records, window=100)

    assert records["200"]["text"] == "ОТРЕДАКТИРОВАНО"   # правка применена
    assert "201" not in records                          # удалён (в окне, не пришёл)
    assert "202" in records                              # новый из окна добавлен
    assert "100" in records                              # вне окна — нетронут
    assert records["5"]["text"] == "закреп обновлён"     # закреп освежён
    assert deleted == 1
    assert refreshed >= 3  # 2 recent + 1 pinned


async def test_refresh_window_no_recent_no_delete():
    # пустой канал/нет последних → ничего не удаляем
    records = {"1": {"id": 1, "text": "пост"}}
    client = _FakeClient(recent=[], pinned=[])
    refreshed, deleted = await history._refresh_window(client, "entity", records, window=100)
    assert deleted == 0 and "1" in records
