"""Резолв путей проекта: корень репозитория, секреты, папки каналов.

Корень ищется так:
1. переменная окружения ``TGCOCKPIT_HOME`` (если задана);
2. ближайший родитель cwd, где лежит ``pyproject.toml`` + ``src/tgcockpit``;
3. ближайший родитель cwd, где есть и ``channels/``, и ``secrets/``;
4. как запасной вариант — текущая директория.

Так инструмент одинаково работает при запуске из корня репозитория
(`uv run ...`) и при установленном пакете, если задать ``TGCOCKPIT_HOME``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache
def repo_root() -> Path:
    env = os.environ.get("TGCOCKPIT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd().resolve()
    for p in (cur, *cur.parents):
        if (p / "pyproject.toml").exists() and (p / "src" / "tgcockpit").is_dir():
            return p
        if (p / "channels").is_dir() and (p / "secrets").is_dir():
            return p
    return cur


# --- секреты (всегда вне channels/, в .gitignore) ---------------------------


def secrets_dir() -> Path:
    return repo_root() / "secrets"


def secrets_env_file() -> Path:
    return secrets_dir() / ".env"


def sessions_dir() -> Path:
    return secrets_dir() / "sessions"


def session_file(account: str) -> Path:
    """Путь к Telethon-сессии аккаунта (без расширения — Telethon добавит .session)."""
    return sessions_dir() / account


# --- рабочие пространства каналов (Слой 2) ----------------------------------


def channels_dir() -> Path:
    return repo_root() / "channels"


def channel_dir(name: str) -> Path:
    return channels_dir() / name


def channel_config_file(name: str) -> Path:
    return channel_dir(name) / "config.yaml"


def channel_claude_file(name: str) -> Path:
    return channel_dir(name) / "CLAUDE.md"


def channel_content_plan_file(name: str) -> Path:
    return channel_dir(name) / "content-plan.md"


def channel_data_dir(name: str) -> Path:
    return channel_dir(name) / "data"


def channel_drafts_dir(name: str) -> Path:
    return channel_dir(name) / "drafts"


def channel_insights_dir(name: str) -> Path:
    return channel_dir(name) / "insights"


def channel_skills_dir(name: str) -> Path:
    """Per-entity скиллы/знания (профиль голоса и т.п.) — изолированы по сущности."""
    return channel_dir(name) / "skills"


def channel_voice_file(name: str) -> Path:
    return channel_skills_dir(name) / "voice.md"


def logs_dir() -> Path:
    return repo_root() / "logs"


# --- runtime-состояние бота (gitignored) ------------------------------------


def state_dir() -> Path:
    return repo_root() / ".state"


def bot_state_file() -> Path:
    """Активная сущность и session_id Claude по каждому Telegram-чату."""
    return state_dir() / "bot_state.json"


def pending_file() -> Path:
    """Очередь действий, ожидающих подтверждения пользователя (публикации и т.п.)."""
    return state_dir() / "pending.json"
