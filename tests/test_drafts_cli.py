"""CLI `drafts list`: агент-дружелюбные форматы (plain/json) и авто-режим под мостом."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from tgcockpit.cli import app
from tgcockpit.storage import drafts as drafts_mod

_BOX = set("─━│┃┌┐└┘├┤┬┴┼┏┓┗┛")  # символы рамки rich-таблицы — их не должно быть в plain/json


def _make_two(channel: str) -> None:
    drafts_mod.new_draft(channel, title="Первый пост", pillar="обучение")
    d2 = drafts_mod.new_draft(channel, title="Второй пост", pillar="кейсы")
    d2.status = "scheduled"
    d2.schedule_at = "2026-06-10T09:00"
    drafts_mod.save(d2)


def test_drafts_list_plain_is_relayable(channel):
    _make_two(channel)
    res = CliRunner().invoke(app, ["drafts", "list", "--channel", channel, "--format", "plain"])
    assert res.exit_code == 0
    assert not (_BOX & set(res.output)), "plain не должен содержать рамку таблицы"
    # нумерованные строки + имя файла (аргумент для post/schedule) + статус
    assert "1." in res.output and "2." in res.output
    assert "Первый пост" in res.output and "scheduled" in res.output
    assert "файл:" in res.output and ".md" in res.output


def test_drafts_list_json_parses(channel):
    _make_two(channel)
    res = CliRunner().invoke(app, ["drafts", "list", "--channel", channel, "--format", "json"])
    assert res.exit_code == 0
    rows = json.loads(res.output.strip())
    assert len(rows) == 2
    assert {"file", "status", "schedule_at", "title", "pillar"} <= set(rows[0])
    assert any(r["status"] == "scheduled" and r["schedule_at"] == "2026-06-10T09:00" for r in rows)


def test_drafts_list_auto_is_plain_under_bridge(channel, monkeypatch):
    _make_two(channel)
    monkeypatch.setenv("TGCOCKPIT_BRIDGE", "1")  # имитируем вызов из-под моста (агент)
    monkeypatch.delenv("TGCOCKPIT_CONFIRM", raising=False)
    res = CliRunner().invoke(app, ["drafts", "list", "--channel", channel, "--format", "auto"])
    assert res.exit_code == 0
    assert not (_BOX & set(res.output)), "под мостом auto обязан давать plain, не таблицу"
    assert "файл:" in res.output


def test_drafts_list_auto_is_table_in_terminal(channel, monkeypatch):
    _make_two(channel)
    monkeypatch.delenv("TGCOCKPIT_BRIDGE", raising=False)  # обычный терминал → таблица
    res = CliRunner().invoke(app, ["drafts", "list", "--channel", channel, "--format", "auto"])
    assert res.exit_code == 0
    assert "Черновики" in res.output  # заголовок rich-таблицы


def test_drafts_list_empty(channel):
    res = CliRunner().invoke(app, ["drafts", "list", "--channel", channel, "--format", "plain"])
    assert res.exit_code == 0
    assert "нет" in res.output.lower()
