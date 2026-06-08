"""Создание опросов и квизов в каналах/группах.

⚠️ Telethon 1.43: ``Poll.question`` и ``PollAnswer.text`` имеют тип ``TextWithEntities``
(не строку!), а ``Poll`` требует позиционный ``hash``. Частая ошибка — передать строку.
``option`` — произвольные байты-идентификаторы вариантов (используем индекс).
"""

from __future__ import annotations

from typing import Any

from telethon import TelegramClient
from telethon.tl.types import (
    InputMediaPoll,
    Poll,
    PollAnswer,
    TextWithEntities,
)

from ..storage import workspace
from ..util.logging import get_logger
from ..util.ratelimit import with_floodwait
from .client import connected, resolve_entity

log = get_logger("polls")


def _twe(s: str) -> TextWithEntities:
    """Обернуть строку в TextWithEntities (без сущностей форматирования)."""
    return TextWithEntities(text=s, entities=[])


def build_poll(
    question: str,
    options: list[str],
    *,
    quiz: bool = False,
    correct: int | None = None,
    multiple: bool = False,
    public_voters: bool = False,
) -> InputMediaPoll:
    """Собрать InputMediaPoll (чистая функция — тестируется без сети)."""
    if len(options) < 2:
        raise ValueError("у опроса должно быть минимум 2 варианта")
    if len(options) > 10:
        raise ValueError("у опроса максимум 10 вариантов")
    if quiz and correct is None:
        raise ValueError("для квиза нужен индекс правильного ответа (correct)")
    if quiz and not (0 <= correct < len(options)):
        raise ValueError("correct вне диапазона вариантов")
    if quiz and multiple:
        raise ValueError("квиз не может быть multiple-choice")

    answers = [
        PollAnswer(text=_twe(opt), option=str(i).encode()) for i, opt in enumerate(options)
    ]
    poll = Poll(
        id=0,  # Telegram присвоит свой
        question=_twe(question),
        answers=answers,
        hash=0,  # обязателен в этой версии Telethon
        quiz=quiz,
        multiple_choice=multiple,
        public_voters=public_voters,
    )
    # ВАЖНО: в Telethon 1.43 InputMediaPoll.correct_answers — это List[int] (индексы
    # правильных вариантов), а НЕ bytes. Проверено сериализацией: [b"1"] даёт struct.error.
    correct_answers = [correct] if quiz else None
    return InputMediaPoll(poll=poll, correct_answers=correct_answers)


async def create_poll(
    channel: str,
    question: str,
    options: list[str],
    *,
    quiz: bool = False,
    correct: int | None = None,
    multiple: bool = False,
    public_voters: bool = False,
) -> dict[str, Any]:
    """Отправить опрос/квиз в сущность (требует изучения — опрос в голосе сущности)."""
    cfg = workspace.require_studied(channel)  # гард: опросы только после изучения
    media = build_poll(
        question, options, quiz=quiz, correct=correct, multiple=multiple, public_voters=public_voters
    )
    async with connected() as client:
        entity = await resolve_entity(client, cfg.handle)
        msg = await with_floodwait(client.send_message, entity, file=media)
    log.info("poll '%s': msg_id=%s (%s вариантов)", channel, msg.id, len(options))
    return {"msg_id": int(msg.id), "options": len(options), "quiz": quiz}
