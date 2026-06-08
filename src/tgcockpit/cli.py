"""Typer CLI — единый контракт, который дёргают и человек, и Claude Code Skills.

Каждая команда — тонкая обёртка над импортируемой логикой пакета. Сетевые команды
гоняют async через ``asyncio.run``. Имя канала всегда явный ``--channel``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import typer
from rich.console import Console

from . import __version__

app = typer.Typer(
    name="tgcockpit",
    help="CLI-фундамент для ведения Telegram-канала: история, аналитика, постинг, планирование.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    """Прогнать async-корутину из синхронной команды Typer."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _fail(msg: str) -> None:
    """Напечатать ошибку красным и выйти с кодом 1."""
    console.print(f"[bold red]Ошибка:[/] {msg}")
    raise typer.Exit(code=1)


def _gate(kind: str, desc: str, kwargs: dict) -> bool:
    """Гейт подтверждения публикаций. True → действие поставлено в очередь (не выполнять).

    Срабатывает только при вызове из-под моста (агент). В терминале выполняется сразу.
    """
    from . import pending

    if pending.needs_approval():
        pid = pending.add(kwargs["channel"], kind, desc, kwargs)
        console.print(
            f"[yellow]⏸ Требует подтверждения пользователя[/] — поставлено в очередь "
            f"(пользователь нажмёт кнопку в Telegram). id={pid}"
        )
        return True
    return False


@app.command()
def version() -> None:
    """Версия инструмента."""
    console.print(f"tgcockpit {__version__}")


# --- Phase 1: auth / whoami -------------------------------------------------


@app.command()
def auth() -> None:
    """Одноразовый интерактивный логин Telethon (телефон → код → 2FA). Пишет сессию."""
    from .telegram import client as tg

    async def _go() -> None:
        me = await tg.interactive_login()
        handle = f"@{me.username}" if me.username else f"id={me.id}"
        console.print(f"[bold green]✓ Залогинен:[/] {me.first_name or ''} {handle}".strip())
        console.print("Сессия сохранена. Дальше команды используют её без повторного логина.")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001 — на верхнем уровне CLI хотим понятное сообщение
        _fail(str(e))


@app.command()
def whoami() -> None:
    """Проверка сессии: печатает текущий аккаунт."""
    from .telegram import client as tg

    async def _go() -> None:
        me = await tg.whoami()
        handle = f"@{me.username}" if me.username else f"id={me.id}"
        console.print(f"[bold]{me.first_name or ''}[/] {handle}  (id={me.id})".strip())

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


# --- Phase 2: channel init / fetch-history ----------------------------------

channel_app = typer.Typer(help="Управление рабочими пространствами каналов.", no_args_is_help=True)
app.add_typer(channel_app, name="channel")


@channel_app.command("init")
def channel_init(
    channel: str = typer.Option(..., "--channel", help="внутреннее имя сущности (папка)"),
    handle: str = typer.Option(..., "--handle", help="@username или числовой id"),
    kind: str = typer.Option("channel", "--kind", help="channel | group | supergroup"),
    timezone: str = typer.Option("Europe/Moscow", "--tz", help="таймзона для планирования"),
    pillar: list[str] = typer.Option([], "--pillar", help="контент-столп (можно несколько)"),
    frequency: str = typer.Option("", "--frequency", help="желаемая частота, свободный текст"),
    slot: list[str] = typer.Option([], "--slot", help='слот по умолчанию, напр. "mon 09:00"'),
    overwrite: bool = typer.Option(False, "--overwrite", help="пересоздать config/память"),
) -> None:
    """Создать скелет сущности: config.yaml, CLAUDE.md, content-plan.md, data/drafts/insights/skills."""
    if kind not in ("channel", "group", "supergroup"):
        _fail("--kind должен быть channel|group|supergroup")
    from .storage import workspace

    try:
        cfg = workspace.init_channel(
            name=channel,
            handle=handle,
            kind=kind,
            timezone=timezone,
            pillars=list(pillar),
            frequency=frequency,
            default_slots=list(slot),
            overwrite=overwrite,
        )
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
    from . import paths

    console.print(f"[bold green]✓ Сущность '{channel}' ({cfg.kind}) готова[/] → {paths.channel_dir(channel)}")
    console.print(f"  handle={cfg.handle}  tz={cfg.timezone}")
    console.print("  дальше: tgcockpit study --channel " + channel)


@app.command("fetch-history")
def fetch_history_cmd(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    limit: int | None = typer.Option(None, "--limit", help="макс. постов за проход"),
    since: str | None = typer.Option(None, "--since", help="дата ISO, тянуть от неё"),
    full: bool = typer.Option(False, "--full", help="полный рефетч (освежить метрики всех)"),
) -> None:
    """Скачать историю + метрики в data/history.json (инкрементально по умолчанию)."""
    from dateutil import parser as dateparser

    from .telegram import history

    since_dt = None
    if since:
        try:
            since_dt = dateparser.parse(since)
        except (ValueError, OverflowError) as e:
            _fail(f"не разобрал дату --since '{since}': {e}")

    async def _go() -> None:
        res = await history.fetch_history(channel, limit=limit, since=since_dt, full=full)
        snap = res["snapshot"]
        console.print(
            f"[bold green]✓[/] {channel}: +{res['new']} новых, "
            f"~{res.get('refreshed', 0)} освежено, -{res.get('deleted', 0)} удалено, "
            f"всего {res['total']} (режим: {snap['fetched_mode']})"
        )
        if snap.get("subscribers"):
            console.print(f"  подписчиков: {snap['subscribers']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


# --- Phase 3: analytics -----------------------------------------------------


@app.command()
def analytics(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    tier: str = typer.Option("auto", "--tier", help="auto|basic|broadcast"),
    no_save: bool = typer.Option(False, "--no-save", help="не писать insights/, только печать"),
) -> None:
    """Tier-1 метрики из кэша (+ проба Tier-2 для крупных каналов). Tier-1 не требует сети."""
    if tier not in ("auto", "basic", "broadcast"):
        _fail("--tier должен быть auto|basic|broadcast")

    from .analytics import compute, report

    async def _probe() -> None:
        # Tier-2 проба (сеть) — обновит config.broadcast_stats, который попадёт в отчёт
        from .config import ChannelConfig
        from .telegram import stats_api
        from .telegram.client import connected

        cfg = ChannelConfig.load(channel)
        async with connected() as client:
            status, data = await stats_api.resolve_tier(client, cfg, tier=tier)
        console.print(f"  Tier-2 broadcast stats: [bold]{status}[/]")
        if data:
            for k, v in data.items():
                if v is not None:
                    console.print(f"    {k}: {v:.0f}")

    try:
        if tier != "basic":
            try:
                _run(_probe())
            except Exception as e:  # noqa: BLE001 — Tier-2 не критичен, мягко падаем в Tier-1
                console.print(f"  [yellow]Tier-2 пропущен[/] ({e}); считаю Tier-1 из кэша.")

        result = compute.compute_report(channel)
        report.render_console(result, console)
        if not no_save:
            paths_out = report.write_insights(channel, result)
            console.print(f"[dim]insights → {paths_out['markdown']}[/]")
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


# --- Phase F: transcribe ----------------------------------------------------


@app.command()
def transcribe(
    file: str = typer.Option(..., "--file", help="путь к аудиофайлу (.ogg/.m4a/.wav)"),
    lang: str = typer.Option("ru", "--lang", help="язык распознавания"),
) -> None:
    """Транскрибировать аудио (Apple Speech → фолбэк Whisper)."""
    from .audio import transcribe as tr

    try:
        text = tr.transcribe(file, lang=lang)
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
    console.print(text)


# --- Phase D: study (изучение сущности) -------------------------------------


@app.command()
def study(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
    full: bool = typer.Option(
        False, "--full/--incremental",
        help="полный рефетч (медленно, ловит правки/удаления старых) или инкремент (по умолч.)",
    ),
    limit: int | None = typer.Option(None, "--limit", help="ограничить число постов за фетч"),
) -> None:
    """Изучить сущность: фетч + аналитика + скаффолд профиля голоса; снимает гард на постинг."""
    from .study import profile

    async def _go() -> None:
        res = await profile.study_entity(channel, full=full, limit=limit)
        console.print(f"[bold green]✓ Изучено '{channel}'[/] (постов: {res['posts']})")
        console.print(f"  профиль голоса → {res['voice_file']}")
        console.print(f"  Obsidian-хранилище → {res['vault']} (постов: {res['vault_posts']})")
        console.print("  теперь можно постить. Уточни голос скиллом /study-entity.")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@app.command("vault")
def vault_build(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
) -> None:
    """Пересобрать Obsidian-хранилище постов из кэша истории (офлайн, только текст)."""
    from .study import vault

    try:
        res = vault.build_vault(channel)
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
    console.print(f"[bold green]✓ Хранилище[/] → {res['vault']}")
    console.print(
        f"  синхронизация: +{res['added']} новых, ~{res['updated']} изменённых, "
        f"-{res['deleted']} удалённых, ={res['unchanged']} без изменений"
    )
    console.print(f"  всего текстовых: {res['posts_written']} (пропущено без текста: {res['skipped_non_text']})")


# --- Phase 4: drafts / post / schedule / scheduled / export -----------------

drafts_app = typer.Typer(help="Черновики постов.", no_args_is_help=True)
app.add_typer(drafts_app, name="drafts")


@drafts_app.command("new")
def drafts_new(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
    title: str = typer.Option(..., "--title", help="заголовок черновика"),
    pillar: str = typer.Option("", "--pillar", help="контент-столп"),
    image: list[str] = typer.Option([], "--image", help="путь к картинке (можно несколько, до 10)"),
) -> None:
    """Создать новый черновик ``<date>-<slug>.md`` с frontmatter (тело — HTML)."""
    from .storage import drafts as drafts_mod

    if len(image) > 10:
        _fail("максимум 10 картинок в альбоме")
    try:
        d = drafts_mod.new_draft(channel, title=title, pillar=pillar, images=list(image))
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
    console.print(f"[bold green]✓ Черновик создан[/] → {d.path}")
    if image:
        console.print(f"  картинок: {len(image)}")


@drafts_app.command("list")
def drafts_list(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    fmt: str = typer.Option(
        "auto", "--format",
        help="auto|table|plain|json. auto = plain под мостом (агент), таблица в терминале.",
    ),
) -> None:
    """Список черновиков канала со статусами.

    Под мостом (агент) выводит ПЛОСКИЙ список — его можно пересказать в чат без таблицы.
    В терминале — человекочитаемая таблица. Файл черновика = аргумент для ``post``/``schedule``.
    """
    from .storage import drafts as drafts_mod

    items = drafts_mod.list_drafts(channel)
    if fmt == "auto":
        from . import pending

        fmt = "plain" if pending.needs_approval() else "table"

    if fmt == "json":
        import json

        rows = [
            {"file": _short_path(d.path), "status": d.status,
             "schedule_at": d.schedule_at, "title": d.title, "pillar": d.pillar}
            for d in items
        ]
        print(json.dumps(rows, ensure_ascii=False))
        return

    if not items:
        console.print("[yellow]Черновиков нет.[/]")
        return

    if fmt == "plain":
        # агент-дружелюбно: одна строка на черновик, без rich-таблицы (её нельзя слать в чат)
        for i, d in enumerate(items, 1):
            when = f", запланирован {d.schedule_at}" if d.schedule_at else ""
            line = f"{i}. {d.title or '(без заголовка)'} — {d.status}{when} · файл: {_short_path(d.path)}"
            console.print(line, markup=False, highlight=False)
        return

    from rich.table import Table

    t = Table(title=f"Черновики {channel}")
    t.add_column("Статус"); t.add_column("Запланирован"); t.add_column("Заголовок"); t.add_column("Файл")
    for d in items:
        t.add_row(d.status, d.schedule_at or "—", d.title or "—", _short_path(d.path))
    console.print(t)


@app.command()
def post(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    draft: str = typer.Option(..., "--draft", help="путь к файлу черновика"),
    now: bool = typer.Option(False, "--now", help="опубликовать немедленно"),
) -> None:
    """Опубликовать черновик сейчас (требует --now как подтверждение)."""
    if not now:
        _fail("публикация сейчас требует флага --now (защита от случайного постинга)")
    if _gate("post", f"📤 Опубликовать черновик {draft.rsplit('/', 1)[-1]}", {"channel": channel, "draft": draft}):
        return
    from .telegram import posting

    async def _go() -> None:
        res = await posting.post_now(channel, draft)
        console.print(f"[bold green]✓ Опубликовано[/] msg_id={res['msg_id']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@app.command()
def schedule(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    draft: str = typer.Option(..., "--draft", help="путь к файлу черновика"),
    at: str = typer.Option(..., "--at", help='время в tz канала, напр. "2026-06-10T09:00"'),
) -> None:
    """Поставить черновик в серверную очередь Telegram (опубликуется при выключенном Mac)."""
    if _gate("schedule", f"🕐 Запланировать {draft.rsplit('/', 1)[-1]} на {at}",
             {"channel": channel, "draft": draft, "at": at}):
        return
    from .telegram import posting

    async def _go() -> None:
        res = await posting.schedule_post(channel, draft, at)
        console.print(
            f"[bold green]✓ В очереди[/] msg_id={res['msg_id']} на {res['schedule_at']} "
            f"(очередь: {res['queue_size']}/{posting.SCHEDULE_LIMIT})"
        )

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


scheduled_app = typer.Typer(help="Серверная очередь отложенных постов.", no_args_is_help=True)
app.add_typer(scheduled_app, name="scheduled")


@scheduled_app.command("list")
def scheduled_list(channel: str = typer.Option(..., "--channel", help="имя канала")) -> None:
    """Показать серверную очередь (источник истины для реконсиляции)."""
    from rich.table import Table

    from .telegram import posting

    async def _go() -> None:
        items = await posting.list_scheduled(channel)
        if not items:
            console.print("[yellow]Очередь пуста.[/]")
            return
        t = Table(title=f"Очередь {channel} ({len(items)}/{posting.SCHEDULE_LIMIT})")
        t.add_column("msg_id", justify="right"); t.add_column("Когда"); t.add_column("Превью")
        for it in items:
            t.add_row(str(it["id"]), it["schedule_at"] or "—", it["preview"])
        console.print(t)

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@scheduled_app.command("cancel")
def scheduled_cancel(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    msg_id: int = typer.Option(..., "--id", help="id отложенного поста"),
) -> None:
    """Отменить отложенный пост по id (чистит отметку в черновике)."""
    from .telegram import posting

    async def _go() -> None:
        res = await posting.cancel_scheduled(channel, msg_id)
        console.print(f"[bold green]✓ Отменено[/] msg_id={res['cancelled']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


# --- Phase B: message CRUD + polls ------------------------------------------

message_app = typer.Typer(help="Редактирование/удаление опубликованных сообщений.", no_args_is_help=True)
app.add_typer(message_app, name="message")


@message_app.command("edit")
def message_edit(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
    msg_id: int = typer.Option(..., "--id", help="id сообщения"),
    text: str = typer.Option(..., "--text", help="новый текст (HTML)"),
) -> None:
    """Отредактировать опубликованное сообщение (HTML-форматирование)."""
    if _gate("edit", f"✏️ Редактировать сообщение {msg_id}",
             {"channel": channel, "msg_id": msg_id, "text": text}):
        return
    from .telegram import posting

    async def _go() -> None:
        res = await posting.edit_message(channel, msg_id, text)
        console.print(f"[bold green]✓ Отредактировано[/] msg_id={res['msg_id']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@message_app.command("delete")
def message_delete(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
    msg_id: int = typer.Option(..., "--id", help="id сообщения"),
) -> None:
    """Удалить сообщение по id (у всех)."""
    if _gate("delete", f"🗑 Удалить сообщение {msg_id}", {"channel": channel, "msg_id": msg_id}):
        return
    from .telegram import posting

    async def _go() -> None:
        res = await posting.delete_message(channel, msg_id)
        console.print(f"[bold green]✓ Удалено[/] msg_id={res['deleted']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


poll_app = typer.Typer(help="Опросы и квизы.", no_args_is_help=True)
app.add_typer(poll_app, name="poll")


@poll_app.command("create")
def poll_create(
    channel: str = typer.Option(..., "--channel", help="имя сущности"),
    question: str = typer.Option(..., "--question", help="вопрос опроса"),
    option: list[str] = typer.Option(..., "--option", help="вариант ответа (2–10, повтори флаг)"),
    quiz: bool = typer.Option(False, "--quiz", help="режим квиза (один правильный)"),
    correct: int | None = typer.Option(None, "--correct", help="индекс правильного (для квиза, с 0)"),
    multiple: bool = typer.Option(False, "--multiple", help="можно выбрать несколько"),
) -> None:
    """Создать опрос/квиз в сущности."""
    if _gate("poll", f"📊 Опрос: {question}",
             {"channel": channel, "question": question, "options": list(option),
              "quiz": quiz, "correct": correct, "multiple": multiple}):
        return
    from .telegram import polls

    async def _go() -> None:
        res = await polls.create_poll(
            channel, question, list(option), quiz=quiz, correct=correct, multiple=multiple
        )
        kind = "квиз" if res["quiz"] else "опрос"
        console.print(f"[bold green]✓ {kind} создан[/] msg_id={res['msg_id']} ({res['options']} вар.)")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


# --- Phase C: groups (read / reply / exclude) -------------------------------

group_app = typer.Typer(help="Группы: чтение сообщений, ответы, исключения.", no_args_is_help=True)
app.add_typer(group_app, name="group")


@group_app.command("read")
def group_read(
    channel: str = typer.Option(..., "--channel", help="имя группы"),
    limit: int = typer.Option(50, "--limit", help="сколько последних сообщений"),
    all_messages: bool = typer.Option(False, "--all", help="не применять исключения"),
) -> None:
    """Прочитать последние сообщения группы (с учётом исключений)."""
    from rich.table import Table

    from .telegram import groups

    async def _go() -> None:
        items = await groups.read_recent(channel, limit=limit, apply_exclusions=not all_messages)
        if not items:
            console.print("[yellow]Сообщений нет (или все исключены).[/]")
            return
        t = Table(title=f"{channel}: последние {len(items)}")
        t.add_column("id", justify="right"); t.add_column("от"); t.add_column("текст")
        for it in items:
            who = ("@" + it["username"]) if it.get("username") else str(it["sender_id"])
            t.add_row(str(it["id"]), who, (it["text"] or "").replace("\n", " ")[:70])
        console.print(t)

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@group_app.command("reply")
def group_reply(
    channel: str = typer.Option(..., "--channel", help="имя группы"),
    to: int = typer.Option(..., "--to", help="id сообщения, на которое отвечаем"),
    text: str | None = typer.Option(None, "--text", help="текст ответа (HTML)"),
    draft: str | None = typer.Option(None, "--draft", help="путь к черновику вместо текста"),
) -> None:
    """Ответить на сообщение в группе (требует изучения сущности)."""
    if _gate("reply", f"↩️ Ответить на сообщение {to}",
             {"channel": channel, "to": to, "text": text, "draft": draft}):
        return
    from .telegram import groups

    async def _go() -> None:
        res = await groups.reply(channel, to, body=text, draft_path=draft)
        console.print(f"[bold green]✓ Ответ отправлен[/] msg_id={res['msg_id']} → {res['reply_to']}")

    try:
        _run(_go())
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


@group_app.command("exclude")
def group_exclude(
    channel: str = typer.Option(..., "--channel", help="имя группы"),
    user: list[str] = typer.Option([], "--user", help="@username или id для игнора"),
    keyword: list[str] = typer.Option([], "--keyword", help="ключевое слово для игнора"),
    msg_id: list[int] = typer.Option([], "--id", help="id сообщения для игнора"),
    show: bool = typer.Option(False, "--show", help="только показать текущие исключения"),
) -> None:
    """Управлять списком исключений при чтении группы (правит config.yaml)."""
    from .config import ChannelConfig

    try:
        cfg = ChannelConfig.load(channel)
    except Exception as e:  # noqa: BLE001
        _fail(str(e))

    if not show:
        cfg.exclusions.users = sorted(set(cfg.exclusions.users) | set(user))
        cfg.exclusions.keywords = sorted(set(cfg.exclusions.keywords) | set(keyword))
        cfg.exclusions.message_ids = sorted(set(cfg.exclusions.message_ids) | set(msg_id))
        cfg.save()
    ex = cfg.exclusions
    console.print(f"[bold]Исключения '{channel}':[/]")
    console.print(f"  users:    {ex.users or '—'}")
    console.print(f"  keywords: {ex.keywords or '—'}")
    console.print(f"  ids:      {ex.message_ids or '—'}")


@app.command()
def export(
    channel: str = typer.Option(..., "--channel", help="имя канала"),
    fmt: str = typer.Option("json", "--format", help="json|csv"),
    out: str | None = typer.Option(None, "--out", help="путь файла (по умолчанию в data/)"),
) -> None:
    """Выгрузить кэш истории в JSON или CSV."""
    import csv as _csv

    from . import paths
    from .storage import jsonstore, workspace

    if fmt not in ("json", "csv"):
        _fail("--format должен быть json|csv")

    raw = jsonstore.read_json(workspace.history_file(channel), default={}) or {}
    posts = list(raw.get("posts", {}).values())
    if not posts:
        _fail(f"нет кэша истории для '{channel}' — сначала fetch-history")

    out_path = paths_for_export(out, channel, fmt)
    if fmt == "json":
        jsonstore.write_json(out_path, raw)
    else:
        cols = ["id", "date", "views", "forwards", "reactions", "replies", "media_kind", "char_count"]
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for p in sorted(posts, key=lambda r: r.get("id", 0)):
                w.writerow(p)
    console.print(f"[bold green]✓ Экспорт[/] → {out_path}")


def paths_for_export(out: str | None, channel: str, fmt: str):
    from pathlib import Path

    from . import paths

    if out:
        return Path(out)
    return paths.channel_data_dir(channel) / f"export.{fmt}"


def _short_path(p: str) -> str:
    from . import paths

    try:
        from pathlib import Path

        return str(Path(p).relative_to(paths.repo_root()))
    except ValueError:
        return p


# --- Phase G: bot (мост к Claude) -------------------------------------------


@app.command()
def bot() -> None:
    """Запустить Telegram-бота — мост к Claude Code (меню + текст/голос → этапы)."""
    from .telegram import bot as bot_mod

    console.print("[bold]Запускаю бота-мост…[/] (Ctrl+C для остановки)")
    try:
        bot_mod.main()
    except KeyboardInterrupt:
        console.print("\nОстановлен.")
    except Exception as e:  # noqa: BLE001
        _fail(str(e))


if __name__ == "__main__":
    app()
