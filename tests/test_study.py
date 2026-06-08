"""Тесты изучения сущности и гарда постинга."""

from __future__ import annotations

from pathlib import Path

import pytest

from tgcockpit import paths
from tgcockpit.config import ChannelConfig
from tgcockpit.storage import drafts as drafts_mod
from tgcockpit.storage import jsonstore, workspace
from tgcockpit.study import profile


def test_require_studied_blocks_unstudied(repo):
    workspace.init_channel("fresh", handle="@fresh")  # studied=False
    with pytest.raises(ValueError, match="не изучена"):
        workspace.require_studied("fresh")


def test_require_studied_passes_when_studied(channel):
    # channel-фикстура уже studied=True
    assert workspace.require_studied(channel).studied is True


async def test_post_blocked_until_studied(repo):
    workspace.init_channel("u", handle="@u")
    d = drafts_mod.new_draft("u", title="x", body="<b>hi</b>")
    from tgcockpit.telegram import posting

    with pytest.raises(ValueError, match="study"):
        await posting.post_now("u", d.path)


async def test_study_entity_builds_voice_and_marks(repo, monkeypatch):
    workspace.init_channel("c", handle="@c")
    # засеять кэш истории, чтобы аналитика отработала без сети
    posts = {
        "1": {
            "id": 1, "date": "2026-05-01T09:00:00", "views": 1000, "reactions": 100,
            "forwards": 0, "replies": 0, "media_kind": "text", "char_count": 200,
            "text_preview": "пример учебного поста",
        }
    }
    jsonstore.write_json(
        workspace.history_file("c"), {"snapshot": {"subscribers": 500}, "posts": posts}
    )

    async def fake_fetch(channel, **kw):
        return {"new": 0, "total": 1, "snapshot": {"subscribers": 500}}

    monkeypatch.setattr(profile.history, "fetch_history", fake_fetch)

    res = await profile.study_entity("c")
    voice = paths.channel_voice_file("c")
    assert voice.exists()
    assert "Профиль голоса" in voice.read_text(encoding="utf-8")
    assert res["posts"] == 1
    cfg = ChannelConfig.load("c")
    assert cfg.studied is True
    assert cfg.studied_at is not None
