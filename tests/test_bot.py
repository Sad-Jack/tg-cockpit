"""Тесты бота: разбиение текста, состояние, сборка диспетчера. Требует aiogram."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("aiogram")

from tgcockpit.telegram import bot  # noqa: E402


def test_split_text_short():
    assert bot.split_text("короткий") == ["короткий"]


def test_split_text_long():
    parts = bot.split_text("a" * 9000)
    assert len(parts) >= 3
    assert all(len(p) <= bot.TG_LIMIT for p in parts)


def test_split_text_by_lines():
    text = "\n".join(["строка"] * 1000)
    parts = bot.split_text(text)
    assert all(len(p) <= bot.TG_LIMIT for p in parts)


async def test_state_active_and_session(repo):
    await bot.set_active(100, "mlchan")
    assert bot.get_active(100) == "mlchan"
    await bot.set_session(100, "s1")
    assert bot.get_session(100) == "s1"
    # смена сущности сбрасывает session
    await bot.set_active(100, "other")
    assert bot.get_session(100) is None
    # изоляция по чату
    assert bot.get_active(999) is None


async def test_touch_sets_last_active(repo):
    assert bot.get_last_active(100) is None
    await bot.touch(100)
    assert bot.get_last_active(100) is not None


async def test_handle_request_announces_new_session_and_streams(repo, monkeypatch):
    await bot.set_active(500, "ch")

    async def fake_run(text, session_id=None, active_channel=None):
        yield {"kind": "session", "session_id": "sess-9"}
        yield {"kind": "tool", "name": "Bash"}
        yield {"kind": "text", "text": "делаю"}
        yield {"kind": "result", "text": "готово", "cost": 0.01, "session_id": "sess-9"}

    monkeypatch.setattr(bot.bridge, "run_request", fake_run)

    sent: list[str] = []

    class _Msg:
        chat = SimpleNamespace(id=500)

        async def answer(self, text, **kw):
            sent.append(text)

    await bot.handle_request(_Msg(), "сделай пост")
    joined = "\n".join(sent)
    # новая сессия → анонс активной сущности (последней использованной)
    assert "Новая сессия" in joined and "ch" in joined
    # текст ответа стримится
    assert "делаю" in joined
    # tool-шум (🔧) НЕ выводим; стоимость ($) НЕ показываем
    assert not any("🔧" in s for s in sent)
    assert not any("$" in s for s in sent)
    # result дублирует text-блок → НЕ задваиваем («готово» из result не должно прийти,
    # т.к. текст уже стримился)
    assert joined.count("делаю") == 1
    assert "готово" not in joined
    # session_id сохранён для resume
    assert bot.get_session(500) == "sess-9"


async def test_thinking_goes_to_console_not_telegram(repo, monkeypatch):
    await bot.set_active(501, "ch")

    async def fake_run(text, session_id=None, active_channel=None):
        yield {"kind": "thinking", "text": "размышляю про [скобки] и <html>"}
        yield {"kind": "text", "text": "ответ"}
        yield {"kind": "result", "text": "ответ", "cost": 0.01, "session_id": "s"}

    monkeypatch.setattr(bot.bridge, "run_request", fake_run)

    printed: list = []
    monkeypatch.setattr(bot._console, "print", lambda *a, **k: printed.append(a))
    monkeypatch.setattr(bot._console, "rule", lambda *a, **k: printed.append(a))

    sent: list[str] = []

    class _Msg:
        chat = SimpleNamespace(id=501)

        async def answer(self, text, **kw):
            sent.append(text)

    await bot.handle_request(_Msg(), "запрос с [markup] символами")
    joined = "\n".join(sent)
    # размышление НЕ уходит в Telegram
    assert "размышляю" not in joined
    # но печатается в консоль (среди прочих событий — запрос, thinking, text, result)
    assert printed  # консоль что-то получила
    # ответ при этом в чат попадает
    assert "ответ" in joined


def test_console_event_is_markup_safe():
    # контент агента со скобками rich-разметки НЕ должен ронять печать
    bot._console_event({"kind": "text", "text": "вот [bold]не тег[/] и [/закрытие]"})
    bot._console_event({"kind": "thinking", "text": "[unclosed"})
    bot._console_event({"kind": "tool", "name": "Bash", "input": {"cmd": "echo [x]"}})
    bot._console_event({"kind": "error", "text": "сбой [code]"})


def test_fmt_tool_input_truncates_long():
    out = bot._fmt_tool_input({"data": "x" * 1000})
    assert out.endswith("…") and len(out) <= 401


class _Target:
    """Заглушка для .answer (Message / callback_query.message)."""

    def __init__(self) -> None:
        self.msgs: list[str] = []
        self.kbs: list[Any] = []

    async def answer(self, text, reply_markup=None, **kw):
        self.msgs.append(text)
        self.kbs.append(reply_markup)


def _callbacks(kb) -> list[str]:
    if kb is None:
        return []
    return [b.callback_data for row in kb.inline_keyboard for b in row]


async def test_handle_request_no_active_single_message_no_entities(repo):
    # нет сущностей → ОДНО сообщение (без дубля), зовущее добавить
    class _Msg:
        chat = SimpleNamespace(id=600)
        sent: list[str] = []

        async def answer(self, text, **kw):
            type(self).sent.append(text)

    _Msg.sent = []
    await bot.handle_request(_Msg(), "привет")
    assert len(_Msg.sent) == 1  # никаких дублей
    assert "Добавить" in _Msg.sent[0] or "добав" in _Msg.sent[0].lower()


async def test_send_menu_empty(repo):
    t = _Target()
    await bot.send_menu(t)
    assert "пока нет" in t.msgs[-1]
    assert "add" in _callbacks(t.kbs[-1])  # только кнопка добавления


async def test_send_menu_lists_entities(repo, monkeypatch):
    from tgcockpit.storage import workspace

    workspace.init_channel("a", handle="@a")
    workspace.init_channel("b", handle="@b")

    async def fake_check(names):
        return {n: {"ok": True} for n in names}

    monkeypatch.setattr(bot.discovery, "check_entities", fake_check)
    t = _Target()
    await bot.send_menu(t)
    cbs = _callbacks(t.kbs[-1])
    assert "sw:a" in cbs and "sw:b" in cbs and "add" in cbs


async def test_send_add_list_discovers_and_stores(repo, monkeypatch):
    found = [
        {"handle": "@a", "title": "Канал A", "kind": "channel", "rights_ok": True, "missing": []},
        {"handle": "@b", "title": "Группа B", "kind": "supergroup", "rights_ok": False,
         "missing": ["delete_messages"]},
    ]

    async def fake_list():
        return found

    monkeypatch.setattr(bot.discovery, "list_admin_entities", fake_list)
    t = _Target()
    await bot.send_add_list(t, 700)
    # фул-права → кнопка pick:0; неполные права → в тексте с «не хватает»
    cbs = _callbacks(t.kbs[-1])
    assert "pick:0" in cbs and "pick:1" not in cbs
    assert any("не хватает" in m for m in t.msgs)
    # discovered сохранён для последующего pick
    assert bot.get_discovered(700) == found


def test_dispatcher_builds(repo):
    assert bot.build_dispatcher() is not None
