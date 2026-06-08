"""Тесты гейта подтверждения публикаций: очередь, диспетчер, CLI-гейт."""

from __future__ import annotations

from tgcockpit import pending


def test_needs_approval_env(monkeypatch):
    monkeypatch.setenv("TGCOCKPIT_BRIDGE", "1")
    monkeypatch.delenv("TGCOCKPIT_CONFIRM", raising=False)
    assert pending.needs_approval() is True
    monkeypatch.setenv("TGCOCKPIT_CONFIRM", "1")
    assert pending.needs_approval() is False  # явное подтверждение снимает гейт
    monkeypatch.delenv("TGCOCKPIT_BRIDGE", raising=False)
    monkeypatch.delenv("TGCOCKPIT_CONFIRM", raising=False)
    assert pending.needs_approval() is False  # вне моста (терминал) — без гейта


def test_pending_roundtrip(repo):
    pid = pending.add("ch", "post", "📤 опубликовать", {"channel": "ch", "draft": "d.md"})
    items = pending.list_pending("ch")
    assert len(items) == 1 and items[0]["id"] == pid and items[0]["kind"] == "post"
    assert pending.list_pending("other") == []  # изоляция по сущности
    rec = pending.pop_by_id(pid)
    assert rec and rec["kwargs"]["draft"] == "d.md"
    assert pending.list_pending("ch") == []  # после pop пусто
    assert pending.pop_by_id(pid) is None  # повторный pop — None


async def test_execute_dispatch_post(repo, monkeypatch):
    called = {}

    async def fake_post(channel, draft):
        called["args"] = (channel, draft)
        return {"msg_id": 7}

    monkeypatch.setattr("tgcockpit.telegram.posting.post_now", fake_post)
    res = await pending.execute({"kind": "post", "kwargs": {"channel": "c", "draft": "d"}})
    assert called["args"] == ("c", "d") and res["msg_id"] == 7


async def test_execute_dispatch_poll(repo, monkeypatch):
    called = {}

    async def fake_poll(channel, question, options, **kw):
        called["q"] = question
        return {"msg_id": 9}

    monkeypatch.setattr("tgcockpit.telegram.polls.create_poll", fake_poll)
    await pending.execute({"kind": "poll", "kwargs": {"channel": "c", "question": "Q?", "options": ["a", "b"]}})
    assert called["q"] == "Q?"


def test_pending_dedup(repo):
    a = pending.add("ch", "post", "📤 пост", {"channel": "ch", "draft": "x.md"})
    b = pending.add("ch", "post", "📤 пост", {"channel": "ch", "draft": "x.md"})
    assert a == b  # идентичное действие не дублируется
    assert len(pending.list_pending("ch")) == 1


def test_cli_post_enqueues_under_bridge(repo, monkeypatch):
    # под мостом (BRIDGE=1, без CONFIRM) post НЕ публикует, а ставит в очередь
    monkeypatch.setenv("TGCOCKPIT_BRIDGE", "1")
    monkeypatch.delenv("TGCOCKPIT_CONFIRM", raising=False)
    from typer.testing import CliRunner

    from tgcockpit.cli import app

    res = CliRunner().invoke(
        app, ["post", "--channel", "ch", "--draft", "channels/ch/drafts/x.md", "--now"]
    )
    assert res.exit_code == 0
    items = pending.list_pending("ch")
    assert len(items) == 1 and items[0]["kind"] == "post"
