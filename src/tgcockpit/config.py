"""Конфигурация: секреты из ``secrets/.env`` + конфиг канала из ``channels/<name>/config.yaml``.

Два независимых уровня:
- :class:`Secrets` — глобальные доступы (api_id/api_hash/токен), грузятся из ``secrets/.env``.
- :class:`ChannelConfig` — настройки конкретного канала (handle, таймзона, столпы…),
  грузятся из ``channels/<name>/config.yaml``. Имя канала всегда явный аргумент —
  глобального состояния нет, два канала не пересекаются.
"""

from __future__ import annotations

from pathlib import Path

import re

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

_NAME_RE = re.compile(r"[\w\-]+", re.UNICODE)


def validate_channel_name(name: str) -> str:
    """Защита от path traversal: имя сущности — только буквы/цифры/_/- (без / и ..).

    Имя уходит в пути (channels/<name>/...) и callback_data — `../../etc` недопустимо.
    """
    if not name or not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"недопустимое имя сущности: {name!r} — только буквы/цифры/_/-, без '/' и '..'"
        )
    return name


def parse_owner_ids(raw: str) -> set[int]:
    """Разобрать список разрешённых Telegram id из строки (через запятую/точку с запятой)."""
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out

from . import paths


class Secrets(BaseSettings):
    """Глобальные доступы. Источник: ``secrets/.env`` (gitignored) + переменные окружения.

    Префикс ``TGCOCKPIT_`` обязателен у всех переменных, чтобы не цеплять чужие.
    """

    model_config = SettingsConfigDict(
        env_file=str(paths.secrets_env_file()),
        env_prefix="TGCOCKPIT_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_id: int = Field(..., description="api_id с my.telegram.org")
    api_hash: str = Field(..., description="api_hash с my.telegram.org")
    account: str = Field("main", description="имя файла сессии в secrets/sessions/")
    bot_token: str | None = Field(None, description="токен бота от @BotFather (опционально)")
    anthropic_api_key: str | None = Field(
        None, description="ключ Anthropic для моста бот↔Claude (или используется логин Claude Code)"
    )
    owner_ids: str = Field(
        "", description="разрешённые Telegram user id через запятую; ПУСТО = бот заблокирован для всех"
    )
    model: str | None = Field(
        None, description="модель Claude для моста (если пусто — модель по умолчанию Claude Code)"
    )
    effort: str | None = Field(
        None, description="reasoning effort моста: low|medium|high|xhigh|max (пусто — по умолчанию)"
    )

    @field_validator("effort")
    @classmethod
    def _check_effort(cls, v: str | None) -> str | None:
        if v not in (None, "", *EFFORT_LEVELS):
            raise ValueError(f"effort должен быть один из: {', '.join(EFFORT_LEVELS)}")
        return v or None


def load_secrets() -> Secrets:
    """Прочитать секреты. Бросает понятную ошибку, если ``secrets/.env`` отсутствует/неполон."""
    env_file = paths.secrets_env_file()
    if not env_file.exists():
        raise FileNotFoundError(
            f"Нет файла секретов: {env_file}\n"
            f"Скопируй шаблон и заполни: cp .env.example secrets/.env"
        )
    return Secrets()  # type: ignore[call-arg]  # значения приходят из env_file


# --- конфиг канала ----------------------------------------------------------


class Exclusions(BaseModel):
    """Исключения при чтении сообщений группы: кого/что игнорировать."""

    users: list[str] = Field(default_factory=list, description="@username или id, чьи сообщения пропускать")
    keywords: list[str] = Field(default_factory=list, description="подстроки — сообщения с ними пропускать")
    message_ids: list[int] = Field(default_factory=list, description="конкретные id сообщений пропускать")


class ChannelConfig(BaseModel):
    """Настройки одной сущности (канала ИЛИ группы) из ``channels/<name>/config.yaml``."""

    name: str
    handle: str = Field(..., description="@username или числовой id сущности")
    kind: str = Field("channel", description="тип: channel | group | supergroup")
    timezone: str = Field("Europe/Moscow", description="таймзона для планирования")
    pillars: list[str] = Field(default_factory=list, description="контент-столпы")
    frequency: str = Field("", description="желаемая частота постинга, свободный текст")
    # запоминаем результат пробы Tier-2, чтобы не дёргать stats API каждый раз
    broadcast_stats: str = Field(
        "unknown", description="доступность Tier-2: unknown|available|unavailable"
    )
    default_slots: list[str] = Field(
        default_factory=list, description='слоты по умолчанию, напр. ["mon 09:00", "thu 18:00"]'
    )
    # изучена ли сущность (построен ли профиль голоса) — гард на постинг
    studied: bool = Field(False, description="запущено ли изучение (есть профиль голоса)")
    studied_at: str | None = Field(None, description="дата последнего изучения (ISO)")
    exclusions: Exclusions = Field(default_factory=Exclusions, description="исключения для чтения групп")

    @classmethod
    def load(cls, channel: str) -> "ChannelConfig":
        validate_channel_name(channel)  # защита от traversal через имя
        cfg_file = paths.channel_config_file(channel)
        if not cfg_file.exists():
            raise FileNotFoundError(
                f"Канал '{channel}' не инициализирован: нет {cfg_file}\n"
                f"Создай: tgcockpit channel init --channel {channel} --handle @..."
            )
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        data.setdefault("name", channel)
        return cls(**data)

    def save(self) -> Path:
        cfg_file = paths.channel_config_file(self.name)
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            yaml.safe_dump(self.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return cfg_file
