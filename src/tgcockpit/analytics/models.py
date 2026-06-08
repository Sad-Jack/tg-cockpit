"""Pydantic-модели аналитики. Структура того, что считает Tier-1 и рендерит report."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PostMetrics(BaseModel):
    """Метрики одного поста (нормализованная запись из history.json)."""

    id: int
    date: str | None = None
    views: int = 0
    forwards: int = 0
    reactions: int = 0
    replies: int = 0
    grouped_id: int | None = None
    media_kind: str = "other"
    char_count: int = 0
    text_preview: str = ""

    @property
    def engagement(self) -> int:
        return self.reactions + self.forwards + self.replies

    @property
    def engagement_rate(self) -> float:
        """Вовлечённость на просмотр (ER). 0, если просмотров нет."""
        return self.engagement / self.views if self.views else 0.0


class TimeSlotStat(BaseModel):
    weekday: int  # 0=Пн … 6=Вс
    hour: int
    posts: int
    avg_views: float
    avg_engagement_rate: float


class MediaStat(BaseModel):
    media_kind: str
    posts: int
    avg_views: float
    avg_engagement_rate: float


class ChannelSnapshot(BaseModel):
    channel: str
    handle: str
    subscribers: int | None = None
    total_posts: int = 0
    avg_views: float = 0.0
    avg_engagement_rate: float = 0.0
    err: float | None = Field(None, description="engagement / подписчики (если известны)")
    broadcast_stats: str = "unknown"


class AnalyticsResult(BaseModel):
    """Итог Tier-1 анализа — то, что сохраняем в insights и печатаем таблицами."""

    snapshot: ChannelSnapshot
    best_times: list[TimeSlotStat] = Field(default_factory=list)
    top_posts: list[PostMetrics] = Field(default_factory=list)
    by_media: list[MediaStat] = Field(default_factory=list)
    length_buckets: dict[str, float] = Field(
        default_factory=dict, description="ER по длине поста: short/medium/long"
    )
    notes: list[str] = Field(default_factory=list, description="наблюдения для человека/мозга")
