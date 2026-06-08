"""Создание/валидация рабочего пространства канала ``channels/<name>/``. Без AI, без сети.

Раскладка (см. план):
    channels/<name>/        # ВСЯ папка channels/ в .gitignore — приватные данные, локально
    ├── config.yaml        # @handle, kind, studied, exclusions
    ├── CLAUDE.md          # память «мозга» сущности
    ├── content-plan.md    # контент-план
    ├── data/              # кэш фетчей (history.json)
    ├── drafts/            # черновики
    ├── insights/          # отчёты аналитики
    ├── skills/            # per-entity знания (voice.md)
    └── <@handle>/         # Obsidian-vault: по .md на текстовый пост + index.md
"""

from __future__ import annotations

from pathlib import Path

from .. import paths
from ..config import ChannelConfig

# Скелет памяти сущности: ТОЛЬКО факты конкретной сущности (метрики, голос, уроки,
# гипотезы, расписание). Глобальные правила живут в корневом CLAUDE.md — здесь их нет.
_CLAUDE_STARTER = """# Channel: {handle} — Working Memory
_Создано через `channel init`. Наполни через `/study-entity` (снимает гард, строит голос), затем при желании `/analyze-channel` (метрики + гипотезы)._

## Snapshot
- Подписчики: ? | Ср. просмотры/пост: ? | Ср. ER: ? | ERR: ?
- Broadcast stats API: ? (зависит от тарифа)
- Таймзона: {tz} | Частота: {freq}

## Аудитория
- (кто подписан, зачем, язык, сегменты)

## Голос и тон
- (детали — в skills/voice.md)

## Контент-столпы
{pillars}

## Выученные уроки (датированные; устаревшие — чистим)
- (появятся после первого анализа)

## Гипотезы (проверяемые; замыкает `/review-performance`)
- (появятся после анализа)

## Расписание постинга
- Слоты по умолчанию: {slots}
"""

# Внутренний файл памяти: markdown-таблица здесь допустима (в Telegram НЕ публикуется).
_CONTENT_PLAN_STARTER = """# Контент-план — {handle}
_Скользящий план. Обновляется через `/content-plan`. Внутренний файл — таблица здесь ок, в Telegram не уходит._

| Дата | Слот | Столп | Тема | Статус |
|------|------|-------|------|--------|
| —    | —    | —     | (пусто) | — |
"""


def channel_exists(name: str) -> bool:
    return paths.channel_config_file(name).exists()


def list_channels() -> list[str]:
    """Имена всех инициализированных сущностей (папки с config.yaml). Для меню бота."""
    base = paths.channels_dir()
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "config.yaml").exists())


def init_channel(
    name: str,
    handle: str,
    kind: str = "channel",
    timezone: str = "Europe/Moscow",
    pillars: list[str] | None = None,
    frequency: str = "",
    default_slots: list[str] | None = None,
    overwrite: bool = False,
) -> ChannelConfig:
    """Создать скелет сущности (канал/группа). Идемпотентно: существующие файлы не трогает."""
    from ..config import validate_channel_name

    validate_channel_name(name)  # защита от path traversal (../) в имени
    # имя уходит в callback_data бота "sw:<name>"/"st:<name>" (лимит Telegram 64 байта);
    # кириллица = 2 байта/символ, поэтому валидируем по БАЙТАМ с запасом на префикс
    if len(name.encode("utf-8")) > 60:
        raise ValueError(
            f"Слишком длинное имя сущности '{name}' "
            f"({len(name.encode('utf-8'))} байт) — максимум 60 байт."
        )
    if channel_exists(name) and not overwrite:
        raise FileExistsError(
            f"Сущность '{name}' уже инициализирована ({paths.channel_dir(name)}). "
            f"Используй overwrite=True, чтобы пересоздать config."
        )

    base = paths.channel_dir(name)
    for sub in ("data", "drafts", "insights", "skills"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    cfg = ChannelConfig(
        name=name,
        handle=handle,
        kind=kind,
        timezone=timezone,
        pillars=pillars or [],
        frequency=frequency,
        default_slots=default_slots or [],
    )
    cfg.save()

    claude = paths.channel_claude_file(name)
    if not claude.exists() or overwrite:
        pillars_md = (
            "\n".join(f"{i}. {p}" for i, p in enumerate(cfg.pillars, 1))
            if cfg.pillars
            else "1. (определить после анализа)"
        )
        claude.write_text(
            _CLAUDE_STARTER.format(
                handle=handle,
                tz=timezone,
                freq=frequency or "?",
                pillars=pillars_md,
                slots=", ".join(cfg.default_slots) or "(не заданы)",
            ),
            encoding="utf-8",
        )

    plan = paths.channel_content_plan_file(name)
    if not plan.exists() or overwrite:
        plan.write_text(_CONTENT_PLAN_STARTER.format(handle=handle), encoding="utf-8")

    return cfg


def require_channel(name: str) -> ChannelConfig:
    """Загрузить конфиг канала или бросить понятную ошибку, что он не инициализирован."""
    return ChannelConfig.load(name)


def require_studied(name: str) -> ChannelConfig:
    """Загрузить конфиг и убедиться, что сущность изучена (флаг ``studied`` в config.yaml).

    Гард на отправку: постить/отвечать до изучения нельзя — иначе агент пишет
    «вслепую», без понимания стиля канала/группы. Проверяется именно флаг ``studied``
    (его ставит ``study``), а не наличие файла voice.md.
    """
    cfg = ChannelConfig.load(name)
    if not cfg.studied:
        raise ValueError(
            f"Сущность '{name}' ещё не изучена — постинг заблокирован. "
            f"Сначала запусти изучение: tgcockpit study --channel {name}"
        )
    return cfg


def history_file(name: str) -> Path:
    return paths.channel_data_dir(name) / "history.json"
