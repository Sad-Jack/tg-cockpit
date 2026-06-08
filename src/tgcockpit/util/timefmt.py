"""tz-aware хелперы планирования. Время всегда привязано к таймзоне канала.

Telethon ждёт aware-datetime для ``schedule=``. Наивное время трактуем в таймзоне
канала, а не в локальной — иначе пост уедет не туда при разнице часовых поясов.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as _dateparser


def channel_tz(tz_name: str) -> ZoneInfo:
    return ZoneInfo(tz_name)


def parse_when(value: str, tz_name: str) -> datetime:
    """Разобрать строку времени и вернуть aware-datetime в таймзоне канала.

    Наивный ввод (``2026-06-10T09:00``) трактуется как локальное время канала.
    Ввод со смещением (``...+03:00``) уважается как есть.
    """
    dt = _dateparser.parse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=channel_tz(tz_name))
    return dt


def now_tz(tz_name: str) -> datetime:
    return datetime.now(channel_tz(tz_name))


def to_utc(dt: datetime) -> datetime:
    """Привести к UTC (Telethon принимает aware-datetime; нормализуем для сравнения)."""
    if dt.tzinfo is None:
        raise ValueError("ожидается aware-datetime")
    return dt.astimezone(timezone.utc)


def humanize(dt: datetime, tz_name: str) -> str:
    """Человекочитаемо в таймзоне канала."""
    return dt.astimezone(channel_tz(tz_name)).strftime("%Y-%m-%d %H:%M %Z")


def is_future(dt: datetime, tz_name: str, min_lead_seconds: int = 60) -> bool:
    """Время в будущем хотя бы на ``min_lead_seconds`` (Telegram не любит «прямо сейчас»)."""
    delta = (to_utc(dt) - now_tz(tz_name).astimezone(timezone.utc)).total_seconds()
    return delta >= min_lead_seconds
