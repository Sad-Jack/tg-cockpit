"""Тесты групп: исключения (чистая логика) + reply dry-run с моками."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tgcockpit.config import Exclusions
from tgcockpit.telegram import groups


# --- is_excluded ------------------------------------------------------------


def _rec(**kw):
    base = {"id": 1, "sender_id": 7, "username": "user", "text": "обычный текст"}
    base.update(kw)
    return base


def test_exclude_by_user_id():
    ex = Exclusions(users=["12345"])
    assert groups.is_excluded(_rec(sender_id=12345), ex) is True


def test_exclude_by_username():
    ex = Exclusions(users=["@spammer"])
    assert groups.is_excluded(_rec(username="spammer"), ex) is True


def test_exclude_by_keyword_case_insensitive():
    ex = Exclusions(keywords=["реклама"])
    assert groups.is_excluded(_rec(text="тут РЕКЛАМА"), ex) is True


def test_exclude_by_message_id():
    ex = Exclusions(message_ids=[999])
    assert groups.is_excluded(_rec(id=999), ex) is True


def test_not_excluded():
    ex = Exclusions(users=["@x"], keywords=["спам"], message_ids=[1])
    assert groups.is_excluded(_rec(id=2, sender_id=7, username="ok", text="норм"), ex) is False


# --- reply (dry-run) --------------------------------------------------------


def _mock_net(monkeypatch, sent_id=321):
    @asynccontextmanager
    async def fake_connected(*a, **k):
        yield SimpleNamespace(send_message=None)  # код берёт client.send_message до with_floodwait

    async def fake_resolve(client, handle):
        return "entity"

    async def fake_floodwait(func, *a, **k):
        return SimpleNamespace(id=sent_id)

    monkeypatch.setattr(groups, "connected", fake_connected)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)
    monkeypatch.setattr(groups, "with_floodwait", fake_floodwait)


async def test_reply_sends(channel, monkeypatch):
    _mock_net(monkeypatch, sent_id=321)
    res = await groups.reply(channel, to_msg_id=10, body="<b>привет</b>")
    assert res["msg_id"] == 321
    assert res["reply_to"] == 10


async def test_reply_blocked_until_studied(repo, monkeypatch):
    from tgcockpit.storage import workspace

    workspace.init_channel("g", handle="@g", kind="supergroup")
    _mock_net(monkeypatch)
    with pytest.raises(ValueError, match="study"):
        await groups.reply("g", to_msg_id=1, body="hi")


async def test_reply_needs_body_or_draft(channel, monkeypatch):
    _mock_net(monkeypatch)
    with pytest.raises(ValueError, match="body"):
        await groups.reply(channel, to_msg_id=1)
