"""Тесты доступа: бот отвечает только владельцу(ам)."""

from __future__ import annotations

from tgcockpit.config import parse_owner_ids
from tgcockpit.telegram import bot


def test_parse_owner_ids():
    assert parse_owner_ids("111, 222 ;333") == {111, 222, 333}
    assert parse_owner_ids("") == set()
    assert parse_owner_ids("abc, 5") == {5}  # мусор игнорируется
    assert parse_owner_ids("-1001234") == {-1001234}


def test_auth_decision_locked_when_no_owner():
    # владелец не задан → заблокировано для всех (включая «владельца»)
    assert bot.auth_decision(123, set()) == "locked"
    assert bot.auth_decision(None, set()) == "locked"


def test_auth_decision_ok_and_denied():
    allowed = {111, 222}
    assert bot.auth_decision(111, allowed) == "ok"
    assert bot.auth_decision(999, allowed) == "denied"  # чужой
    assert bot.auth_decision(None, allowed) == "denied"


def test_compose_allowed_session_owner_auto():
    # владелец из сессии добавляется автоматически (без ручного owner_ids)
    assert bot.compose_allowed(555, set()) == {555}
    # + доп. id из конфига
    assert bot.compose_allowed(555, {777}) == {555, 777}
    # нет сессии и нет конфига → пусто (бот заблокирован)
    assert bot.compose_allowed(None, set()) == set()
    # нет сессии, но есть конфиг → работает по конфигу
    assert bot.compose_allowed(None, {777}) == {777}
