"""Тесты построения опросов/квизов (структура Telethon)."""

from __future__ import annotations

import pytest
from telethon.tl.types import InputMediaPoll, TextWithEntities

from tgcockpit.telegram.polls import build_poll


def test_build_poll_structure():
    m = build_poll("Какой формат?", ["Лонгриды", "Короткие", "Видео"])
    assert isinstance(m, InputMediaPoll)
    # КЛЮЧЕВОЕ: question и answers — TextWithEntities, не строки
    assert isinstance(m.poll.question, TextWithEntities)
    assert m.poll.question.text == "Какой формат?"
    assert all(isinstance(a.text, TextWithEntities) for a in m.poll.answers)
    assert [a.option for a in m.poll.answers] == [b"0", b"1", b"2"]
    assert m.poll.quiz is False
    assert m.correct_answers is None


def test_build_quiz():
    m = build_poll("2+2?", ["3", "4", "5"], quiz=True, correct=1)
    assert m.poll.quiz is True
    assert m.correct_answers == [1]  # индексы-int, не bytes (Telethon 1.43)


def test_validation_min_options():
    with pytest.raises(ValueError, match="минимум 2"):
        build_poll("q", ["one"])


def test_validation_max_options():
    with pytest.raises(ValueError, match="максимум 10"):
        build_poll("q", [str(i) for i in range(11)])


def test_validation_quiz_needs_correct():
    with pytest.raises(ValueError, match="индекс правильного"):
        build_poll("q", ["a", "b"], quiz=True)


def test_validation_quiz_correct_in_range():
    with pytest.raises(ValueError, match="вне диапазона"):
        build_poll("q", ["a", "b"], quiz=True, correct=5)


def test_validation_quiz_not_multiple():
    with pytest.raises(ValueError, match="не может быть multiple"):
        build_poll("q", ["a", "b"], quiz=True, correct=0, multiple=True)


async def test_create_poll_blocked_until_studied(repo):
    # опросы — это отправка сообщения, должны требовать изучения сущности
    from tgcockpit.storage import workspace
    from tgcockpit.telegram import polls

    workspace.init_channel("p", handle="@p")  # studied=False
    with pytest.raises(ValueError, match="study"):
        await polls.create_poll("p", "Q", ["a", "b"])
