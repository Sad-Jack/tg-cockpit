"""Тесты Tier-1 аналитики: чистые функции на синтетических постах."""

from __future__ import annotations

from tgcockpit.analytics import compute
from tgcockpit.analytics.models import PostMetrics
from tgcockpit.storage import jsonstore, workspace


def _post(**kw) -> PostMetrics:
    base = dict(id=1, date="2026-05-01T09:00:00", views=1000, forwards=0, reactions=0, replies=0)
    base.update(kw)
    return PostMetrics(**base)


def test_engagement_rate_basic():
    p = _post(views=1000, reactions=50, forwards=30, replies=20)
    assert p.engagement == 100
    assert p.engagement_rate == 0.1


def test_engagement_rate_zero_views_safe():
    assert _post(views=0, reactions=10).engagement_rate == 0.0


def test_best_times_orders_by_er():
    posts = [
        _post(id=1, date="2026-05-04T09:00:00", views=1000, reactions=100),  # Пн 09 ER .10
        _post(id=2, date="2026-05-05T18:00:00", views=1000, reactions=50),   # Вт 18 ER .05
    ]
    best = compute.best_times(posts)
    assert best[0].weekday == 0 and best[0].hour == 9
    assert best[0].avg_engagement_rate > best[1].avg_engagement_rate


def test_by_media_grouping():
    posts = [
        _post(id=1, media_kind="photo", views=1000, reactions=10),
        _post(id=2, media_kind="photo", views=1000, reactions=30),
        _post(id=3, media_kind="text", views=1000, reactions=100),
    ]
    stats = {m.media_kind: m for m in compute.by_media(posts)}
    assert stats["photo"].posts == 2
    assert stats["text"].avg_engagement_rate > stats["photo"].avg_engagement_rate


def test_length_buckets():
    posts = [
        _post(id=1, char_count=100, views=1000, reactions=10),   # short
        _post(id=2, char_count=600, views=1000, reactions=20),   # medium
        _post(id=3, char_count=2000, views=1000, reactions=5),   # long
    ]
    lb = compute.length_buckets(posts)
    assert set(lb) == {"short", "medium", "long"}
    assert lb["medium"] > lb["long"]


def test_top_posts():
    posts = [_post(id=i, reactions=i * 10, views=1000) for i in range(1, 6)]
    top = compute.top_posts(posts, n=2)
    assert [p.id for p in top] == [5, 4]


def test_compute_report_err_with_subscribers(channel):
    # положим кэш истории с подписчиками
    posts = {
        str(i): {
            "id": i, "date": f"2026-05-0{i}T09:00:00", "views": 1000,
            "forwards": 0, "reactions": 100, "replies": 0, "media_kind": "text",
            "char_count": 200,
        }
        for i in range(1, 4)
    }
    jsonstore.write_json(
        workspace.history_file(channel),
        {"snapshot": {"subscribers": 1000}, "posts": posts},
    )
    res = compute.compute_report(channel)
    assert res.snapshot.total_posts == 3
    assert res.snapshot.avg_engagement_rate == 0.1
    # ERR = avg engagement (100) / subscribers (1000) = 0.1
    assert abs(res.snapshot.err - 0.1) < 1e-9
    assert res.notes  # сгенерированы наблюдения


def test_compute_report_no_subscribers_err_none(channel):
    posts = {"1": {"id": 1, "date": "2026-05-01T09:00:00", "views": 500, "reactions": 10}}
    jsonstore.write_json(workspace.history_file(channel), {"snapshot": {}, "posts": posts})
    res = compute.compute_report(channel)
    assert res.snapshot.err is None
