"""Изучение сущности и сборка профиля голоса ``channels/<name>/skills/voice.md``.

CLI-часть (эта) делает «механику»: тянет историю, считает аналитику, собирает выборку
постов и пишет СКАФФОЛД voice.md с реальными данными + слотами под стиль; ставит
``studied=True``. Дальше скилл-агент `/study-entity` дочитывает примеры и наполняет
профиль смыслом (тон, голос, правила) — и сам же его потом обновляет.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import paths
from ..analytics import compute
from ..config import ChannelConfig
from ..telegram import history
from ..util.logging import get_logger
from . import vault

log = get_logger("study")

_VOICE_SCAFFOLD = """# Профиль голоса — {handle} ({kind})
_Скаффолд от `study` {today}. Наполняется/обновляется скиллом `/study-entity`._

## Что это за сущность
- Тип: {kind}
- Тема: (определить по выборке ниже — о чём канал/группа)
- Аудитория: (кто читатели, зачем подписаны)

## Голос и тон (заполняет агент по примерам)
- Делать: …
- Не делать: …
- Длина типичного поста: ~{avg_chars} знаков
- Эмодзи: (как используются)

## Форматирование
- Telegram-разметка: HTML (`<b>`, `<i>`, `<code>`, `<a href>` …). НЕ markdown `**…**`.

## Данные (из аналитики)
- Постов в выборке: {total}
- Ср. просмотры: {avg_views} | Ср. ER: {avg_er}

## Примеры удачных постов (топ по вовлечённости)
{samples}

## Правила стиля (выводит и поддерживает агент)
- (появятся после анализа примеров)
"""


async def study_entity(
    channel: str, full: bool = False, sample: int = 6, limit: int | None = None
) -> dict[str, Any]:
    """Изучить сущность: фетч + аналитика + скаффолд профиля голоса + studied=True.

    По умолчанию инкрементально: первый запуск (пустой кэш) тянет всё, дальше — только
    новые посты. ``full=True`` — полный рефетч (медленно на больших каналах, но ловит
    правки/удаления старых постов и освежает метрики). ``limit`` — ограничить фетч.
    """
    cfg = ChannelConfig.load(channel)

    # 1) свежие данные (сеть). Инкрементально по max(id); для групп iter_messages так же.
    fetch = await history.fetch_history(channel, full=full, limit=limit)

    # 2) аналитика из кэша (офлайн) + примеры
    posts, _ = compute.load_posts(channel)
    result = compute.compute_report(channel)
    top = sorted(posts, key=lambda p: p.engagement, reverse=True)[:sample]
    avg_chars = int(sum(p.char_count for p in posts) / len(posts)) if posts else 0
    samples = (
        "\n".join(
            f"- (ER {p.engagement_rate:.1%}, {p.views} просм.) {p.text_preview.strip() or '—'}"
            for p in top
        )
        or "- (постов в кэше нет)"
    )

    # 3) скаффолд профиля голоса
    voice = paths.channel_voice_file(channel)
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_text(
        _VOICE_SCAFFOLD.format(
            handle=cfg.handle,
            kind=cfg.kind,
            today=date.today().isoformat(),
            avg_chars=avg_chars,
            total=result.snapshot.total_posts,
            avg_views=f"{result.snapshot.avg_views:.0f}",
            avg_er=f"{result.snapshot.avg_engagement_rate:.1%}",
            samples=samples,
        ),
        encoding="utf-8",
    )

    # 4) собрать Obsidian-хранилище (системно, только текст)
    vault_info = vault.build_vault(channel)

    # 5) пометить изученной
    cfg.studied = True
    cfg.studied_at = date.today().isoformat()
    cfg.save()

    log.info("study '%s': studied=True, профиль → %s, vault → %s", channel, voice, vault_info["vault"])
    return {
        "channel": channel,
        "voice_file": str(voice),
        "posts": result.snapshot.total_posts,
        "fetched_new": fetch["new"],
        "studied_at": cfg.studied_at,
        "vault": vault_info["vault"],
        "vault_posts": vault_info["posts_written"],
    }
