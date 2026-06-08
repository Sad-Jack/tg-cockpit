"""Dry-run постинга: реконсиляция, лимит, анти-дубль — с моками Telethon. Плюс timefmt."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tgcockpit.storage import drafts as drafts_mod
from tgcockpit.telegram import posting
from tgcockpit.util import timefmt


# --- timefmt ----------------------------------------------------------------


def test_parse_when_naive_gets_channel_tz():
    dt = timefmt.parse_when("2026-06-10T09:00", "Europe/Moscow")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 3 * 3600  # MSK = UTC+3


def test_parse_when_respects_explicit_offset():
    dt = timefmt.parse_when("2026-06-10T09:00:00+00:00", "Europe/Moscow")
    assert dt.utcoffset().total_seconds() == 0


def test_is_future():
    future = timefmt.now_tz("Europe/Moscow") + timedelta(hours=1)
    past = timefmt.now_tz("Europe/Moscow") - timedelta(hours=1)
    assert timefmt.is_future(future, "Europe/Moscow")
    assert not timefmt.is_future(past, "Europe/Moscow")


def test_is_future_needs_lead_time():
    soon = timefmt.now_tz("Europe/Moscow") + timedelta(seconds=10)
    assert not timefmt.is_future(soon, "Europe/Moscow", min_lead_seconds=60)


# --- хелперы мока сети ------------------------------------------------------


def _mock_network(monkeypatch, queue, sent_id=999):
    async def fake_list(channel):
        return queue

    @asynccontextmanager
    async def fake_connected(*a, **k):
        yield SimpleNamespace()

    async def fake_resolve(client, handle):
        return "entity"

    async def fake_send(client, entity, draft, schedule):
        return SimpleNamespace(id=sent_id)

    monkeypatch.setattr(posting, "list_scheduled", fake_list)
    monkeypatch.setattr(posting, "connected", fake_connected)
    monkeypatch.setattr(posting, "resolve_entity", fake_resolve)
    monkeypatch.setattr(posting, "_send", fake_send)


def _future_str():
    return (timefmt.now_tz("Europe/Moscow") + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")


# --- schedule_post ----------------------------------------------------------


async def test_schedule_writes_frontmatter(channel, monkeypatch):
    _mock_network(monkeypatch, queue=[], sent_id=12345)
    d = drafts_mod.new_draft(channel, title="Пост", body="Тело")

    res = await posting.schedule_post(channel, d.path, _future_str())
    assert res["msg_id"] == 12345

    again = drafts_mod.load(Path(d.path))
    assert again.status == "scheduled"
    assert again.scheduled_msg_id == 12345
    assert again.schedule_at is not None


async def test_schedule_rejects_past(channel, monkeypatch):
    _mock_network(monkeypatch, queue=[])
    d = drafts_mod.new_draft(channel, title="X")
    with pytest.raises(ValueError, match="не в будущем"):
        await posting.schedule_post(channel, d.path, "2000-01-01T09:00")


async def test_schedule_blocks_duplicate(channel, monkeypatch):
    # черновик уже стоит в очереди с тем же id → отказ во избежание дубля
    _mock_network(monkeypatch, queue=[{"id": 777, "schedule_at": None, "preview": ""}])
    d = drafts_mod.new_draft(channel, title="Dup")
    d.scheduled_msg_id = 777
    drafts_mod.save(d)
    with pytest.raises(ValueError, match="уже запланирован"):
        await posting.schedule_post(channel, d.path, _future_str())


async def test_schedule_enforces_limit(channel, monkeypatch):
    full_queue = [{"id": i, "schedule_at": None, "preview": ""} for i in range(posting.SCHEDULE_LIMIT)]
    _mock_network(monkeypatch, queue=full_queue)
    d = drafts_mod.new_draft(channel, title="Overflow")
    with pytest.raises(ValueError, match="очередь заполнена"):
        await posting.schedule_post(channel, d.path, _future_str())


async def test_post_now_sets_posted(channel, monkeypatch):
    _mock_network(monkeypatch, queue=[], sent_id=42)
    d = drafts_mod.new_draft(channel, title="Now")
    res = await posting.post_now(channel, d.path)
    assert res["msg_id"] == 42
    again = drafts_mod.load(Path(d.path))
    assert again.status == "posted"


async def test_cancel_clears_frontmatter(channel, monkeypatch):
    _mock_network(monkeypatch, queue=[])
    d = drafts_mod.new_draft(channel, title="ToCancel")
    d.scheduled_msg_id = 555
    d.status = "scheduled"
    drafts_mod.save(d)

    # для cancel мокаем сам Telethon-вызов удаления
    async def fake_floodwait(func, *a, **k):
        return None

    monkeypatch.setattr(posting, "with_floodwait", fake_floodwait)
    res = await posting.cancel_scheduled(channel, 555)
    assert res["cancelled"] == 555
    again = drafts_mod.load(Path(d.path))
    assert again.scheduled_msg_id is None
    assert again.status == "draft"


def test_resolve_media_missing_raises(repo):
    with pytest.raises(FileNotFoundError):
        posting._resolve_media(["nope/missing.jpg"])


# --- edit / delete (dry-run) ------------------------------------------------


def _mock_edit_delete(monkeypatch, sent_id=777):
    @asynccontextmanager
    async def fake_connected(*a, **k):
        # атрибуты-заглушки: код берёт client.edit_message/delete_messages до with_floodwait
        yield SimpleNamespace(edit_message=None, delete_messages=None)

    async def fake_resolve(client, handle):
        return "entity"

    async def fake_floodwait(func, *a, **k):
        return SimpleNamespace(id=sent_id)

    monkeypatch.setattr(posting, "connected", fake_connected)
    monkeypatch.setattr(posting, "resolve_entity", fake_resolve)
    monkeypatch.setattr(posting, "with_floodwait", fake_floodwait)


async def test_edit_message(channel, monkeypatch):
    _mock_edit_delete(monkeypatch, sent_id=55)
    res = await posting.edit_message(channel, 55, "<b>новый</b>")
    assert res["msg_id"] == 55
    assert res["status"] == "edited"


async def test_delete_message_clears_draft(channel, monkeypatch):
    _mock_edit_delete(monkeypatch)
    d = drafts_mod.new_draft(channel, title="X")
    d.scheduled_msg_id = 888
    d.status = "scheduled"
    drafts_mod.save(d)

    res = await posting.delete_message(channel, 888)
    assert res["deleted"] == 888
    again = drafts_mod.load(Path(d.path))
    assert again.scheduled_msg_id is None
    assert again.status == "draft"
