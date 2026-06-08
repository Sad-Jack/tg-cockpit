"""Tier-1 аналитика — чистые функции из кэша, без сети.

Считает то, что доступно любому каналу из ``history.json``: вовлечённость, ERR,
лучшее время постинга, топ-посты, перформанс по типам медиа и по длине поста.
Никакого глобального состояния — на вход список записей, на выход модели.
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean

from ..storage import jsonstore, workspace
from .models import (
    AnalyticsResult,
    ChannelSnapshot,
    MediaStat,
    PostMetrics,
    TimeSlotStat,
)


def load_posts(channel: str) -> tuple[list[PostMetrics], dict]:
    """Прочитать кэш истории канала → (метрики постов, raw snapshot из фетча)."""
    raw = jsonstore.read_json(workspace.history_file(channel), default={}) or {}
    posts = [PostMetrics(**rec) for rec in raw.get("posts", {}).values()]
    posts.sort(key=lambda p: p.id)
    return posts, raw.get("snapshot", {})


def _avg(xs: list[float]) -> float:
    return float(mean(xs)) if xs else 0.0


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def best_times(posts: list[PostMetrics], top: int = 5) -> list[TimeSlotStat]:
    """Слоты (день недели × час) с лучшим средним перформансом."""
    buckets: dict[tuple[int, int], list[PostMetrics]] = {}
    for p in posts:
        dt = _parse_dt(p.date)
        if dt is None:
            continue
        buckets.setdefault((dt.weekday(), dt.hour), []).append(p)
    stats = [
        TimeSlotStat(
            weekday=wd,
            hour=hr,
            posts=len(group),
            avg_views=_avg([p.views for p in group]),
            avg_engagement_rate=_avg([p.engagement_rate for p in group]),
        )
        for (wd, hr), group in buckets.items()
    ]
    # сортируем по ER, затем по просмотрам; слоты с одним постом не приоритезируем вслепую
    stats.sort(key=lambda s: (s.avg_engagement_rate, s.avg_views), reverse=True)
    return stats[:top]


def top_posts(posts: list[PostMetrics], n: int = 5) -> list[PostMetrics]:
    """Топ постов по абсолютной вовлечённости."""
    return sorted(posts, key=lambda p: p.engagement, reverse=True)[:n]


def by_media(posts: list[PostMetrics]) -> list[MediaStat]:
    """Перформанс по типу медиа."""
    buckets: dict[str, list[PostMetrics]] = {}
    for p in posts:
        buckets.setdefault(p.media_kind, []).append(p)
    stats = [
        MediaStat(
            media_kind=kind,
            posts=len(group),
            avg_views=_avg([p.views for p in group]),
            avg_engagement_rate=_avg([p.engagement_rate for p in group]),
        )
        for kind, group in buckets.items()
    ]
    stats.sort(key=lambda s: s.avg_engagement_rate, reverse=True)
    return stats


def length_buckets(posts: list[PostMetrics]) -> dict[str, float]:
    """Средний ER по длине поста: short (<300), medium (300–1000), long (>1000)."""
    groups: dict[str, list[float]] = {"short": [], "medium": [], "long": []}
    for p in posts:
        if p.char_count < 300:
            groups["short"].append(p.engagement_rate)
        elif p.char_count <= 1000:
            groups["medium"].append(p.engagement_rate)
        else:
            groups["long"].append(p.engagement_rate)
    return {k: _avg(v) for k, v in groups.items() if v}


_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _notes(best: list[TimeSlotStat], lengths: dict[str, float]) -> list[str]:
    notes: list[str] = []
    if best:
        b = best[0]
        notes.append(
            f"Лучший слот: {_WD[b.weekday]} {b.hour:02d}:00 "
            f"(ER ~{b.avg_engagement_rate:.1%}, ср. просмотры {b.avg_views:.0f})"
        )
    if lengths:
        best_len = max(lengths, key=lengths.get)  # type: ignore[arg-type]
        ru = {"short": "короткие (<300)", "medium": "средние (300–1000)", "long": "длинные (>1000)"}
        notes.append(f"Лучше заходит по длине: {ru[best_len]} (ER ~{lengths[best_len]:.1%})")
    return notes


def compute_report(channel: str, top_n: int = 5) -> AnalyticsResult:
    """Полный Tier-1 отчёт по каналу из кэша. Без сети."""
    posts, snap = load_posts(channel)
    cfg = workspace.require_channel(channel)
    subscribers = snap.get("subscribers")

    avg_views = _avg([p.views for p in posts])
    avg_er = _avg([p.engagement_rate for p in posts])
    avg_engagement = _avg([float(p.engagement) for p in posts])
    err = (avg_engagement / subscribers) if subscribers else None

    best = best_times(posts)
    lengths = length_buckets(posts)

    snapshot = ChannelSnapshot(
        channel=channel,
        handle=cfg.handle,
        subscribers=subscribers,
        total_posts=len(posts),
        avg_views=avg_views,
        avg_engagement_rate=avg_er,
        err=err,
        broadcast_stats=cfg.broadcast_stats,
    )
    return AnalyticsResult(
        snapshot=snapshot,
        best_times=best,
        top_posts=top_posts(posts, top_n),
        by_media=by_media(posts),
        length_buckets=lengths,
        notes=_notes(best, lengths),
    )
