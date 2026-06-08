"""Рендер аналитики: rich-таблицы в консоль + сохранение в ``insights/<date>.md`` и ``.json``.

Markdown-инсайт — это то, что Claude Code дистиллирует в ``CLAUDE.md``; JSON — машинный
срез для дальнейшей обработки. Дата в имени файла — из системных часов (обычный Python).
"""

from __future__ import annotations

from datetime import date

from rich.console import Console
from rich.table import Table

from .. import paths
from ..storage import jsonstore
from .models import AnalyticsResult

_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def render_console(result: AnalyticsResult, console: Console | None = None) -> None:
    """Напечатать отчёт таблицами rich."""
    console = console or Console()
    s = result.snapshot

    head = Table(title=f"Канал {s.handle} — Tier-1 аналитика", show_header=False, box=None)
    head.add_row("Постов в кэше", str(s.total_posts))
    head.add_row("Подписчиков", str(s.subscribers) if s.subscribers else "—")
    head.add_row("Ср. просмотры/пост", f"{s.avg_views:.0f}")
    head.add_row("Ср. ER (вовлечённость/просмотр)", f"{s.avg_engagement_rate:.1%}")
    head.add_row("ERR (вовлечённость/подписчик)", f"{s.err:.2%}" if s.err is not None else "—")
    head.add_row("Broadcast stats (Tier-2)", s.broadcast_stats)
    console.print(head)

    if result.best_times:
        t = Table(title="Лучшее время постинга")
        t.add_column("День"); t.add_column("Час"); t.add_column("Постов", justify="right")
        t.add_column("Ср. просмотры", justify="right"); t.add_column("ER", justify="right")
        for b in result.best_times:
            t.add_row(_WD[b.weekday], f"{b.hour:02d}:00", str(b.posts), f"{b.avg_views:.0f}", f"{b.avg_engagement_rate:.1%}")
        console.print(t)

    if result.by_media:
        t = Table(title="Перформанс по типу медиа")
        t.add_column("Тип"); t.add_column("Постов", justify="right")
        t.add_column("Ср. просмотры", justify="right"); t.add_column("ER", justify="right")
        for m in result.by_media:
            t.add_row(m.media_kind, str(m.posts), f"{m.avg_views:.0f}", f"{m.avg_engagement_rate:.1%}")
        console.print(t)

    if result.top_posts:
        t = Table(title="Топ постов по вовлечённости")
        t.add_column("id", justify="right"); t.add_column("Просмотры", justify="right")
        t.add_column("Вовлечённость", justify="right"); t.add_column("Превью")
        for p in result.top_posts:
            t.add_row(str(p.id), f"{p.views}", f"{p.engagement}", p.text_preview.replace("\n", " ")[:60])
        console.print(t)

    if result.length_buckets:
        t = Table(title="ER по длине поста")
        t.add_column("Длина"); t.add_column("Ср. ER", justify="right")
        ru = {"short": "короткие (<300)", "medium": "средние (300–1000)", "long": "длинные (>1000)"}
        for k, v in result.length_buckets.items():
            t.add_row(ru.get(k, k), f"{v:.1%}")
        console.print(t)

    for note in result.notes:
        console.print(f"  • {note}")


def _to_markdown(result: AnalyticsResult, today: str) -> str:
    s = result.snapshot
    lines = [
        f"# Инсайты {s.handle} — {today}",
        "",
        "## Snapshot",
        f"- Постов в кэше: {s.total_posts}",
        f"- Подписчиков: {s.subscribers if s.subscribers else '—'}",
        f"- Ср. просмотры/пост: {s.avg_views:.0f}",
        f"- Ср. ER: {s.avg_engagement_rate:.1%}",
        f"- ERR: {f'{s.err:.2%}' if s.err is not None else '—'}",
        f"- Broadcast stats (Tier-2): {s.broadcast_stats}",
        "",
    ]
    if result.best_times:
        lines += ["## Лучшее время постинга", "", "| День | Час | Постов | Ср. просмотры | ER |", "|---|---|---|---|---|"]
        lines += [
            f"| {_WD[b.weekday]} | {b.hour:02d}:00 | {b.posts} | {b.avg_views:.0f} | {b.avg_engagement_rate:.1%} |"
            for b in result.best_times
        ]
        lines.append("")
    if result.by_media:
        lines += ["## Перформанс по типу медиа", "", "| Тип | Постов | Ср. просмотры | ER |", "|---|---|---|---|"]
        lines += [f"| {m.media_kind} | {m.posts} | {m.avg_views:.0f} | {m.avg_engagement_rate:.1%} |" for m in result.by_media]
        lines.append("")
    if result.top_posts:
        lines += ["## Топ постов", "", "| id | Просмотры | Вовлечённость | Превью |", "|---|---|---|---|"]
        lines += [
            f"| {p.id} | {p.views} | {p.engagement} | {p.text_preview.replace(chr(10), ' ')[:60]} |"
            for p in result.top_posts
        ]
        lines.append("")
    if result.notes:
        lines += ["## Наблюдения", ""] + [f"- {n}" for n in result.notes] + [""]
    return "\n".join(lines)


def write_insights(channel: str, result: AnalyticsResult, today: str | None = None) -> dict[str, str]:
    """Сохранить отчёт в ``insights/<date>.md`` и ``insights/<date>.json``. Вернуть пути."""
    today = today or date.today().isoformat()
    insights = paths.channel_insights_dir(channel)
    insights.mkdir(parents=True, exist_ok=True)

    md_path = insights / f"{today}.md"
    md_path.write_text(_to_markdown(result, today), encoding="utf-8")

    json_path = insights / f"{today}.json"
    jsonstore.write_json(json_path, result.model_dump())

    return {"markdown": str(md_path), "json": str(json_path)}
