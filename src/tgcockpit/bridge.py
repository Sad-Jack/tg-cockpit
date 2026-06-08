"""Мост «бот ↔ headless Claude Code» через claude-agent-sdk.

Бот не «умный» — он передаёт запрос пользователя в headless-инстанс Claude Code,
запущенный в директории проекта (cwd). Claude читает CLAUDE.md проекта, COMMANDS.md
(гид по командам) и channels/<X>/, дёргает CLI-команды как инструменты и отдаёт
этапы рассуждения. Эта обёртка превращает сообщения SDK в простые dict-события,
которые бот шлёт в Telegram «по этапам».

События (`kind`):
- ``session``  — {session_id}: для продолжения диалога (resume).
- ``thinking`` — {text}: блок «размышления» (extended thinking) — для консоли, не в чат.
- ``tool``     — {name, input}: агент вызвал инструмент (этап).
- ``text``     — {text}: текстовый блок рассуждения/ответа.
- ``result``   — {text, cost, session_id}: финал.
- ``error``    — {text}: ошибка/недоступность.

SDK — опциональная зависимость (extra ``bot``); импорт ленивый.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from . import paths
from .config import load_secrets
from .util.logging import get_logger

log = get_logger("bridge")

# Язык агента: всё, что видит пользователь (ответы, пояснения, рассуждения,
# промежуточные шаги), — строго на русском. Применяется и в системном промпте
# (append к claude_code preset), и в каждом запросе (build_prompt) — чтобы
# исключить англоязычную «болтовню» агента, которая стримится в чат.
_LANG_RULE = (
    "Общайся с пользователем ТОЛЬКО на русском языке. Все ответы, пояснения, "
    "рассуждения и промежуточные шаги пиши на русском — даже когда просто "
    "комментируешь, что собираешься сделать. Английский в тексте для пользователя "
    "недопустим (на английском могут быть только команды, код, имена файлов и идентификаторы)."
)


def _api_key() -> str | None:
    """Ключ Anthropic из secrets (если задан). Иначе — используется логин Claude Code."""
    try:
        secrets = load_secrets()
    except Exception:  # noqa: BLE001 — секретов может не быть при тестах
        return None
    return getattr(secrets, "anthropic_api_key", None)


def model_effort() -> tuple[str | None, str | None]:
    """(model, effort) из secrets. None означает «по умолчанию Claude Code»."""
    try:
        secrets = load_secrets()
    except Exception:  # noqa: BLE001
        return None, None
    return getattr(secrets, "model", None), getattr(secrets, "effort", None)


def describe_model() -> str:
    """Человекочитаемая строка для старта консоли: какая модель и effort включены."""
    model, effort = model_effort()
    return (
        f"модель: {model or 'по умолчанию (настройка Claude Code)'} · "
        f"effort: {effort or 'по умолчанию'}"
    )


def _build_options(cwd: Path, resume: str | None, api_key: str | None = None) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    kwargs: dict[str, Any] = dict(
        cwd=str(cwd),
        setting_sources=["project", "user"],  # подхватить CLAUDE.md/.claude/MCP-серверы проекта+юзера
        # bypassPermissions: в headless негде показать диалог разрешений, поэтому разрешаем
        # все инструменты (Canva и др. MCP, Bash, файлы) без запросов. Публикация в Telegram
        # ВСЁ РАВНО под гейтом подтверждения (env TGCOCKPIT_BRIDGE → pending.py): только по кнопке.
        permission_mode="bypassPermissions",
        resume=resume,
        # claude_code preset = тот же системный промпт, что и по умолчанию (None),
        # плюс append с правилом языка — авторитетно и переживает resume (кэшируется).
        system_prompt={"type": "preset", "preset": "claude_code", "append": _LANG_RULE},
    )
    # TGCOCKPIT_BRIDGE=1 включает гейт подтверждения: публикующие команды у агента
    # не выполняются, а ставятся в очередь (см. pending.py). Ключ — тоже в env агента,
    # а не в глобальный os.environ (не светим в ps).
    env = {"TGCOCKPIT_BRIDGE": "1"}
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    kwargs["env"] = env

    model, effort = model_effort()
    if model:
        kwargs["model"] = model
    if effort:
        kwargs["effort"] = effort
    return ClaudeAgentOptions(**kwargs)


def classify(message: Any) -> list[dict[str, Any]]:
    """Превратить сообщение SDK в список событий. Duck-typing — устойчиво к версиям SDK."""
    events: list[dict[str, Any]] = []
    cls = type(message).__name__

    if cls == "SystemMessage":
        data = getattr(message, "data", {}) if isinstance(getattr(message, "data", {}), dict) else {}
        sid = data.get("session_id")
        model = data.get("model")  # реальная модель, которую выбрал Claude Code
        if sid or model:
            ev: dict[str, Any] = {"kind": "session"}
            if sid:
                ev["session_id"] = sid
            if model:
                ev["model"] = model
            events.append(ev)
        return events

    if cls == "AssistantMessage":
        for block in getattr(message, "content", []) or []:
            bcls = type(block).__name__
            text = getattr(block, "text", None)
            if bcls == "ThinkingBlock" or hasattr(block, "thinking"):
                thinking = getattr(block, "thinking", None)
                if thinking:
                    events.append({"kind": "thinking", "text": thinking})
            elif bcls == "TextBlock" or (text and not hasattr(block, "name")):
                if text:
                    events.append({"kind": "text", "text": text})
            elif bcls == "ToolUseBlock" or hasattr(block, "name"):
                events.append(
                    {"kind": "tool", "name": getattr(block, "name", "?"),
                     "input": getattr(block, "input", {})}
                )
        return events

    if cls == "ResultMessage":
        events.append(
            {
                "kind": "result",
                "text": getattr(message, "result", "") or "",
                "cost": getattr(message, "total_cost_usd", None),
                "session_id": getattr(message, "session_id", None),
            }
        )
    return events


def build_prompt(prompt: str, active_channel: str | None) -> str:
    """Подмешать правило языка и контекст активной сущности к запросу пользователя."""
    if not active_channel:
        return f"[{_LANG_RULE}]\n\n{prompt}"
    return (
        f"[{_LANG_RULE}]\n\n"
        f"Активная сущность (канал/группа): {active_channel}\n"
        f"Работай в её директории channels/{active_channel}/ "
        f"(читай channels/{active_channel}/CLAUDE.md и skills/voice.md).\n\n"
        f"Запрос пользователя: {prompt}"
    )


async def run_request(
    prompt: str,
    *,
    session_id: str | None = None,
    active_channel: str | None = None,
    cwd: Path | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Прогнать запрос через headless Claude Code, выдавая события по мере поступления."""
    try:
        from claude_agent_sdk import query
    except ImportError:
        yield {"kind": "error", "text": "claude-agent-sdk не установлен. Поставь: uv sync --extra bot"}
        return

    cwd = cwd or paths.repo_root()
    full_prompt = build_prompt(prompt, active_channel)
    options = _build_options(cwd, session_id, _api_key())

    try:
        async for message in query(prompt=full_prompt, options=options):
            for ev in classify(message):
                yield ev
    except Exception as e:  # noqa: BLE001 — на верхнем уровне отдаём ошибку в чат
        log.warning("bridge error: %s", e)
        yield {"kind": "error", "text": str(e)}
