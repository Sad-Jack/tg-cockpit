"""Telegram-бот — тонкий МОСТ между пользователем и Claude Code.

Минимум интерфейса: меню кнопок для добавления/переключения активной сущности
(канал/группа). Всё остальное — текст или голос — уходит в :mod:`tgcockpit.bridge`
(headless Claude Code), а этапы рассуждения стримятся обратно в чат отдельными
сообщениями (как в консоли — поэтому ПЛАЙН-текст, без parse_mode: рассуждение агента
не гарантированно валидный HTML; HTML применяется к РЕАЛЬНЫМ постам в канал через Telethon).

Добавление сущностей: бот сам перечисляет каналы/группы, где у пользователя есть
админка с нужными правами (см. :mod:`tgcockpit.telegram.discovery`) — выбор кнопкой,
без ручного ввода @handle. Меню показывает активные сущности с проверкой актуальности.

Жизненный цикл сессии: каждое сообщение поднимает сессию Claude. После ``IDLE_SECONDS``
(30 мин) тишины сессия «гасится» (session_id сбрасывается), следующий запрос — с чистого
контекста, и бот пишет активную (последнюю) сущность.

Голос: скачивается и транскрибируется (Apple Speech → Whisper), затем — как текст.

``aiogram`` — опциональная зависимость (extra ``bot``); импорт ленивый.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from .. import bridge, paths
from ..config import load_secrets
from ..storage import jsonstore, workspace
from ..util.logging import get_logger
from . import discovery

log = get_logger("bot")

# Живая консоль агента: весь поток (запрос → размышления → инструменты → ответ)
# печатается в stdout, как в обычном CLI Claude Code. В Telegram уходит только
# текст/результат (см. handle_request); консоль показывает ВСЁ, включая thinking и tool-вызовы.
_console = Console()

_model_logged = False  # реальную модель логируем один раз за процесс (из init SDK)


def _log_real_model(model: str | None) -> None:
    global _model_logged
    if model and not _model_logged:
        _, eff = bridge.model_effort()
        log.info("Claude использует модель: %s · effort: %s", model, eff or "по умолчанию")
        _model_logged = True

TG_LIMIT = 4096
IDLE_SECONDS = 30 * 60  # 30 мин без сообщений → сессия Claude «выключается»

try:
    from aiogram import BaseMiddleware, Bot, Dispatcher, F
    from aiogram.filters import Command, CommandObject
    from aiogram.types import (
        BotCommand,
        CallbackQuery,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        MenuButtonCommands,
        Message,
    )

    _AIOGRAM_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - зависит от extra
    _AIOGRAM_ERROR = exc


# --- состояние (по чату: active / session_id / last_active / discovered) -----

_state_lock = asyncio.Lock()


def _load_state() -> dict[str, Any]:
    return jsonstore.read_json(paths.bot_state_file(), default={}) or {}


def _save_state(state: dict[str, Any]) -> None:
    jsonstore.write_json(paths.bot_state_file(), state)


def _chat(state: dict[str, Any], chat_id: int) -> dict[str, Any]:
    return state.setdefault(str(chat_id), {})


def get_active(chat_id: int) -> str | None:
    return _chat(_load_state(), chat_id).get("active")


def get_session(chat_id: int) -> str | None:
    return _chat(_load_state(), chat_id).get("session_id")


def get_last_active(chat_id: int) -> float | None:
    return _chat(_load_state(), chat_id).get("last_active")


def get_discovered(chat_id: int) -> list[dict[str, Any]]:
    return _chat(_load_state(), chat_id).get("discovered", [])


async def set_active(chat_id: int, name: str) -> None:
    async with _state_lock:
        state = _load_state()
        _chat(state, chat_id)["active"] = name
        _chat(state, chat_id)["session_id"] = None  # новый контекст при смене сущности
        _save_state(state)


async def set_session(chat_id: int, session_id: str | None) -> None:
    async with _state_lock:
        state = _load_state()
        _chat(state, chat_id)["session_id"] = session_id
        _save_state(state)


async def set_discovered(chat_id: int, items: list[dict[str, Any]]) -> None:
    async with _state_lock:
        state = _load_state()
        _chat(state, chat_id)["discovered"] = items
        _save_state(state)


async def touch(chat_id: int) -> None:
    async with _state_lock:
        state = _load_state()
        _chat(state, chat_id)["last_active"] = time.time()
        _save_state(state)


# --- утилиты ----------------------------------------------------------------


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Порезать длинный текст под лимит Telegram, по возможности по строкам."""
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts or [""]


def _require_aiogram() -> None:
    if _AIOGRAM_ERROR is not None:
        raise RuntimeError(
            "Бот требует extra 'bot': uv sync --extra bot\n(aiogram + claude-agent-sdk)"
        ) from _AIOGRAM_ERROR


# --- авторизация: бот отвечает ТОЛЬКО владельцу(ам) ------------------------------


def allowed_ids() -> set[int]:
    """Доп. разрешённые id из конфига (owner_ids). Обычно пусто — владелец берётся из сессии."""
    try:
        from ..config import load_secrets, parse_owner_ids

        return parse_owner_ids(load_secrets().owner_ids)
    except Exception:  # noqa: BLE001
        return set()


async def session_owner_id() -> int | None:
    """Telegram id владельца = аккаунт за user-сессией (api_id/api_hash). None, если не авторизован."""
    try:
        from .client import whoami

        me = await whoami()
        return int(getattr(me, "id", 0)) or None
    except Exception:  # noqa: BLE001
        return None


def compose_allowed(owner: int | None, config_ids: set[int]) -> set[int]:
    """Итоговый allowlist: владелец сессии + (опционально) доп. id из конфига."""
    out = set(config_ids)
    if owner:
        out.add(owner)
    return out


def auth_decision(uid: int | None, allowed: set[int]) -> str:
    """Решение доступа: 'locked' (владелец не задан) | 'ok' | 'denied'."""
    if not allowed:
        return "locked"
    if uid in allowed:
        return "ok"
    return "denied"


async def _deny(event: Any, decision: str, uid: int | None) -> None:
    """Отказать в доступе: чужому — «запрещено», при незаданном владельце — показать его id."""
    if decision == "locked":
        msg = (
            f"⛔ Владелец не определён — доступ закрыт.\n"
            f"Скорее всего не выполнен вход в Telegram: запусти `tgcockpit auth` — "
            f"после этого владельцем автоматически станет твой аккаунт.\n"
            f"(Либо вручную: secrets/.env → TGCOCKPIT_OWNER_IDS={uid})"
        )
    else:
        msg = "⛔ Доступ запрещён."
    try:
        if hasattr(event, "data"):  # CallbackQuery → всплывающий алерт
            await event.answer(msg[:190], show_alert=True)
        else:  # Message
            await event.answer(msg)
    except Exception:  # noqa: BLE001
        pass


def _add_button() -> "InlineKeyboardButton":
    return InlineKeyboardButton(text="➕ Добавить канал/группу", callback_data="add")


def _net_hint(e: Exception) -> str:
    """Человеческое объяснение сетевой ошибки (с подсказкой про auth, если сессия не готова)."""
    s = str(e)
    low = s.lower()
    if "сесси" in low or "not authorized" in low or "auth" in low:
        return (
            "user-сессия не авторизована. Выполни на компьютере один раз:\n"
            "  uv run tgcockpit auth"
        )
    return s or "сбой сети"


# --- меню (список активных сущностей + проверка актуальности) ----------------


async def send_menu(target: Any) -> None:
    """Показать меню: активные сущности (с проверкой актуальности) + кнопка добавления.

    ``target`` — объект с ``.answer`` (Message или callback_query.message).
    """
    names = workspace.list_channels()
    if not names:
        await target.answer(
            "Каналов и групп пока нет.\nНажми «Добавить» — покажу те, где у тебя есть админка.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_add_button()]]),
        )
        return

    # актуальность: резолвятся ли сущности сейчас (сеть). При сбое — статус «неизвестно»,
    # НЕ выдаём всё за доступное; даём понятную подсказку (часто это незавершённый auth).
    hint: str | None = None
    try:
        statuses = await discovery.check_entities(names)
    except Exception as e:  # noqa: BLE001
        statuses = {n: {"ok": None} for n in names}
        hint = _net_hint(e)
        log.warning("check_entities: %s", e)

    rows: list[list[InlineKeyboardButton]] = []
    lines = ["Активные каналы/группы:"]
    if hint:
        lines.append(f"ℹ️ Не удалось проверить: {hint}")
    for name in names:
        st = statuses.get(name, {"ok": None})
        ok = st.get("ok")
        mark = "✓" if ok is True else ("❓" if ok is None else "⚠")
        rows.append([InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"sw:{name}")])
        if ok is False:
            lines.append(f"⚠ {name} — недоступен ({st.get('reason', 'проверь права/доступ')})")
        elif ok is None:
            lines.append(f"❓ {name} — не удалось проверить доступность")
    rows.append([_add_button()])
    await target.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def send_add_list(target: Any, chat_id: int) -> None:
    """Показать каналы/группы, где есть админка, для добавления одной кнопкой."""
    await target.answer("🔎 Ищу каналы и группы, где бот добавлен админом…")
    try:
        found = await discovery.list_admin_entities()
    except Exception as e:  # noqa: BLE001
        await target.answer(f"❌ Не смог получить список: {_net_hint(e)}")
        return

    await set_discovered(chat_id, found)
    footer = (
        "\n\nНужного канала/группы нет в списке?\n"
        "Добавь бота админом со ВСЕМИ правами в этот канал/группу "
        "и снова открой /menu."
    )

    if not found:
        await target.answer(
            "Не нашёл каналов/групп, где бот добавлен администратором." + footer
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    partial: list[str] = []
    for idx, f in enumerate(found):
        if f["rights_ok"]:
            rows.append(
                [InlineKeyboardButton(text=f"➕ {f['title']} ({f['kind']})", callback_data=f"pick:{idx}")]
            )
        else:
            partial.append(f"⚠ {f['title']} ({f['kind']}) — не хватает прав: {', '.join(f['missing'])}")

    if not rows:
        # все найденные без нужных прав — одно понятное сообщение (без дубля футера)
        await target.answer("Везде не хватает прав:\n" + "\n".join(partial) + footer)
        return

    lines = ["Выбери, что добавить (бот здесь админ):", *partial]
    await target.answer(
        "\n".join(lines) + footer,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# --- живая консоль агента (как обычный CLI Claude Code) ----------------------


def _fmt_tool_input(data: Any) -> str:
    """Однострочный компактный вид входных данных инструмента (длинное — обрезаем)."""
    try:
        s = json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = str(data)
    return s if len(s) <= 400 else s[:400] + "…"


def _console_request(chat_id: int, active: str | None, text: str) -> None:
    """Печать входящего запроса в консоль (заголовок + текст пользователя)."""
    _console.rule(Text(f"▶ Запрос · chat {chat_id} · сущность: {active or '—'}", style="bold cyan"))
    _console.print(Text(text))


def _console_event(ev: dict[str, Any]) -> None:
    """Печать одного события агента в консоль. Контент агента — через Text (без markup-инъекций)."""
    kind = ev.get("kind")
    if kind == "thinking":
        body = (ev.get("text") or "").strip()
        if body:
            _console.print(Text(f"💭 {body}", style="dim italic"))
    elif kind == "text":
        body = (ev.get("text") or "").strip()
        if body:
            line = Text("🤖 ", style="bold green")
            line.append(body)
            _console.print(line)
    elif kind == "tool":
        line = Text(f"🔧 {ev.get('name', '?')} ", style="bold yellow")
        line.append(_fmt_tool_input(ev.get("input", {})), style="dim")
        _console.print(line)
    elif kind == "session":
        if ev.get("model"):
            _console.print(Text(f"· модель: {ev['model']}", style="dim"))
    elif kind == "result":
        cost = ev.get("cost")
        tail = f" · ${cost:.4f}" if isinstance(cost, (int, float)) else ""
        _console.print(Text(f"✓ готово{tail}", style="bold green"))
    elif kind == "error":
        _console.print(Text(f"✗ {ev.get('text', 'ошибка')}", style="bold red"))


# --- обработка запроса через мост к Claude -----------------------------------


async def handle_request(message: "Message", text: str) -> None:
    """Передать текст активной сущности в Claude и стримить этапы в чат."""
    chat_id = message.chat.id

    last = get_last_active(chat_id)
    new_session = last is None or (time.time() - last > IDLE_SECONDS)
    if new_session:
        await set_session(chat_id, None)
    await touch(chat_id)

    active = get_active(chat_id)
    _console_request(chat_id, active, text)  # эхо запроса в консоль (как в CLI)

    if not active:
        # одно понятное сообщение (без дублей), с учётом «есть ли вообще сущности»
        prefix = "🆕 Новая сессия. " if new_session else ""
        if not workspace.list_channels():
            await message.answer(
                prefix + "Каналов и групп пока нет — добавь через /menu → ➕ Добавить."
            )
        else:
            await message.answer(prefix + "Сначала выбери канал/группу: /menu")
        return

    if new_session:
        await message.answer(f"🆕 Новая сессия. Активна: {active} (последняя использованная).")

    # без сообщения «передаю Claude» — только ненавязчивый индикатор «печатает…»
    try:
        await message.bot.send_chat_action(chat_id, "typing")
    except Exception:  # noqa: BLE001
        pass
    session_id = get_session(chat_id)
    streamed = False  # отправляли ли уже текст ответа (чтобы не задвоить result)

    async for ev in bridge.run_request(text, session_id=session_id, active_channel=active):
        _console_event(ev)  # зеркалим ВЕСЬ поток в консоль (thinking/tool/text/result)
        kind = ev.get("kind")
        if kind == "session":
            if ev.get("session_id"):
                await set_session(chat_id, ev["session_id"])
            _log_real_model(ev.get("model"))  # реальную модель — один раз за процесс
        elif kind == "text":
            body = ev.get("text", "")
            if body.strip():
                await _send_md(message, body)
                streamed = True
        elif kind == "result":
            if ev.get("session_id"):
                await set_session(chat_id, ev["session_id"])
            body = ev.get("text", "")
            # result обычно дублирует последний text-блок → шлём только если ничего не слали
            if body.strip() and not streamed:
                await _send_md(message, body)
        elif kind == "error":
            await message.answer(f"❌ {ev.get('text', 'ошибка')}")
        # tool-события (🔧 Read и т.п.) намеренно НЕ выводим — это шум

    # агент не публикует сам: показываем действия, поставленные на подтверждение, с кнопками
    await _present_pending(message, active)


async def _present_pending(target: Any, channel: str) -> None:
    """Показать действия из очереди на подтверждение с кнопками ✅/✖️."""
    from .. import pending

    for it in pending.list_pending(channel):
        await target.answer(
            f"Подтверди действие:\n• {it['desc']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Выполнить", callback_data=f"do:{it['id']}"),
                    InlineKeyboardButton(text="✖️ Отмена", callback_data=f"undo:{it['id']}"),
                ]]
            ),
        )


async def _send_md(target: Any, text: str) -> None:
    """Отправить текст агента, отрендерив Markdown→Telegram-HTML (фолбэк в plain при сбое)."""
    from ..util.formatting import md_to_telegram_html

    for chunk in split_text(text):
        if not chunk.strip():
            continue
        try:
            await target.answer(md_to_telegram_html(chunk), parse_mode="HTML")
        except Exception:  # noqa: BLE001 — кривой HTML → отправляем как обычный текст
            await target.answer(chunk)


# --- построение диспетчера ---------------------------------------------------


def build_dispatcher(allowed: set[int] | None = None) -> "Dispatcher":
    """Собрать Dispatcher со всеми хэндлерами (после _require_aiogram).

    ``allowed`` — итоговый allowlist Telegram id (владелец сессии + доп. из конфига).
    Если None — берём только конфиг (owner_ids); владельца сессии подмешивает run_bot.
    """
    dp = Dispatcher()

    # ГЕЙТ ДОСТУПА: бот реагирует только на владельца(ев). Иначе любой, кто найдёт бота,
    # сможет управлять каналами. Проверка — outer-middleware, до всех хэндлеров.
    if allowed is None:
        allowed = allowed_ids()

    class _Auth(BaseMiddleware):
        async def __call__(self, handler, event, data):  # type: ignore[override]
            user = getattr(event, "from_user", None)
            decision = auth_decision(getattr(user, "id", None), allowed)
            if decision == "ok":
                return await handler(event, data)
            await _deny(event, decision, getattr(user, "id", None))
            return None

    dp.message.outer_middleware(_Auth())
    dp.callback_query.outer_middleware(_Auth())

    @dp.message(Command("start"))
    async def _start(message: "Message") -> None:
        await message.answer(
            "Это мост к Claude для управления каналами/группами.\n"
            "Открой /menu — выбери или добавь сущность, потом пиши задачи текстом или голосом."
        )
        await send_menu(message)

    @dp.message(Command("menu"))
    async def _menu(message: "Message") -> None:
        await send_menu(message)

    @dp.message(Command("add"))
    async def _add(message: "Message", command: "CommandObject") -> None:
        # без аргументов — показываем найденные сущности; с аргументами — ручное добавление
        args = (command.args or "").split()
        if not args:
            await send_add_list(message, message.chat.id)
            return
        if len(args) < 2:
            await message.answer("Формат: /add <имя> <@handle|id> [channel|group|supergroup]\n(или /add без аргументов — выбрать из списка)")
            return
        name, handle = args[0], args[1]
        kind = args[2] if len(args) > 2 else "channel"
        try:
            workspace.init_channel(name=name, handle=handle, kind=kind)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"❌ {e}")
            return
        await set_active(message.chat.id, name)
        await _offer_study(message, name)

    @dp.callback_query(F.data.startswith("do:"))
    async def _do(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        pid = cq.data.split(":", 1)[1]
        await cq.answer("Выполняю…")
        from .. import pending

        rec = pending.pop_by_id(pid)
        if not rec:
            await cq.message.answer("Действие уже выполнено или отменено.")
            return
        try:
            await pending.execute(rec)
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ {e}")
            return
        await cq.message.answer(f"✅ Выполнено: {rec['desc']}")

    @dp.callback_query(F.data.startswith("undo:"))
    async def _undo(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        pid = cq.data.split(":", 1)[1]
        from .. import pending

        pending.pop_by_id(pid)
        await cq.answer("Отменено")
        await cq.message.answer("✖️ Отменено, публиковать не буду.")

    @dp.callback_query(F.data == "add")
    async def _cb_add(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        await cq.answer()
        await send_add_list(cq.message, cq.message.chat.id)

    @dp.callback_query(F.data.startswith("pick:"))
    async def _pick(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        await cq.answer()
        try:
            idx = int(cq.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await cq.message.answer("Кнопка устарела — открой /menu → Добавить заново.")
            return
        found = get_discovered(cq.message.chat.id)
        if not (0 <= idx < len(found)):
            await cq.message.answer("Список устарел — открой /menu → Добавить заново.")
            return
        item = found[idx]
        name = discovery.unique_name(discovery.suggest_name(item["handle"], item["title"]))
        try:
            workspace.init_channel(name=name, handle=item["handle"], kind=item["kind"])
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ {e}")
            return
        await set_active(cq.message.chat.id, name)
        await cq.message.answer(f"✓ Добавлено: {item['title']} → '{name}' ({item['kind']}), выбрано активным.")
        await _offer_study(cq.message, name)

    @dp.callback_query(F.data.startswith("sw:"))
    async def _switch(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        name = cq.data.split(":", 1)[1]
        from ..config import ChannelConfig

        try:
            cfg = ChannelConfig.load(name)  # валидируем до смены активной
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ {e}")
            await cq.answer()
            return
        await set_active(cq.message.chat.id, name)
        await cq.answer()
        if not cfg.studied:
            await _offer_study(cq.message, name, f"⇄ Активна: {name} ({cfg.kind}) — но ещё не изучена.")
        else:
            await cq.message.answer(f"⇄ Активна: {name} ({cfg.kind}). Пиши задачу.")

    @dp.callback_query(F.data.startswith("st:"))
    async def _study(cq: "CallbackQuery") -> None:
        if cq.message is None:
            await cq.answer("Недоступно в этом контексте")
            return
        name = cq.data.split(":", 1)[1]
        await cq.answer("Запускаю изучение…")
        await cq.message.answer(f"🔍 Изучаю '{name}'…")
        from ..study import profile

        try:
            res = await profile.study_entity(name)
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ {e}")
            return
        await cq.message.answer(
            f"✓ Изучено '{name}' (постов: {res['posts']}). "
            f"Хранилище: {res['vault_posts']} текстовых постов. Можно постить."
        )

    @dp.message(F.voice)
    async def _voice(message: "Message") -> None:
        _fd, _p = tempfile.mkstemp(suffix=".ogg")
        os.close(_fd)  # закрываем дескриптор сразу (иначе утечка на каждое голосовое)
        tmp = Path(_p)
        try:
            await message.bot.download(message.voice, destination=str(tmp))
            await message.answer("🎙️ расшифровываю…")
            from ..audio import transcribe as tr

            text = await asyncio.to_thread(tr.transcribe, str(tmp), "ru")
        except Exception as e:  # noqa: BLE001
            await message.answer(f"❌ не смог расшифровать: {e}")
            return
        finally:
            tmp.unlink(missing_ok=True)
        await message.answer(f"📝 «{text}»")
        await handle_request(message, text)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def _text(message: "Message") -> None:
        await handle_request(message, message.text)

    return dp


async def _offer_study(target: Any, name: str, head: str | None = None) -> None:
    """Сообщение с кнопкой «Изучить» для свежедобавленной/неизученной сущности."""
    text = head or f"'{name}' добавлена. Запусти изучение перед постингом."
    await target.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔍 Изучить", callback_data=f"st:{name}")]]
        ),
    )


async def run_bot() -> None:
    """Запустить бота (long-polling). Блокируется до остановки."""
    _require_aiogram()
    secrets = load_secrets()
    if not secrets.bot_token:
        raise RuntimeError(
            "Не задан токен бота. Получи у @BotFather и впиши в secrets/.env: "
            "TGCOCKPIT_BOT_TOKEN=..."
        )

    # владелец = аккаунт за user-сессией (автоматически), + опц. доп. id из конфига
    owner = await session_owner_id()
    config_ids = allowed_ids()
    allowed = compose_allowed(owner, config_ids)

    bot = Bot(token=secrets.bot_token)
    dp = build_dispatcher(allowed)
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Каналы/группы: выбрать или добавить"),
            BotCommand(command="add", description="Добавить канал/группу"),
            BotCommand(command="start", description="Старт"),
        ]
    )
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:  # noqa: BLE001 - не критично
        log.warning("set_chat_menu_button: %s", e)

    log.info("Бот-мост запущен (long-polling)")
    _m, _e = bridge.model_effort()
    if _m or _e:  # показываем только ЯВНО заданную модель/effort; иначе — реальную при 1м запросе
        log.info("Claude (из конфига): модель=%s · effort=%s", _m or "по умолчанию", _e or "по умолчанию")
    if allowed:
        log.info(
            "Доступ разрешён id: %s (владелец сессии: %s%s)",
            sorted(allowed),
            owner if owner else "не определён",
            f", доп. из конфига: {sorted(config_ids)}" if config_ids else "",
        )
    else:
        log.warning(
            "⚠ Владелец не определён (user-сессия не авторизована и owner_ids пуст) — бот заблокирован. "
            "Выполни вход (tgcockpit auth) или задай TGCOCKPIT_OWNER_IDS в secrets/.env."
        )
    await dp.start_polling(bot)  # сам закрывает сессию бота при завершении
    log.info("Бот остановлен")


def main() -> None:
    """Синхронная обёртка для запуска из CLI."""
    asyncio.run(run_bot())


if __name__ == "__main__":  # pragma: no cover
    main()
