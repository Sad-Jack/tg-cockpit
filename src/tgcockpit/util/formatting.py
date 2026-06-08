"""Форматирование текста для Telegram. По умолчанию — **HTML**.

Почему HTML, а не Markdown: парсеры Markdown в Telethon (user-сессия) и в Bot API
(MarkdownV2) РАЗНЫЕ и капризные. В MarkdownV2 нужно экранировать 18 спецсимволов,
а Telethon-Markdown — свой диалект (`**`, `__`, `~~`). HTML одинаково работает в обоих
стеках, и экранировать надо лишь три символа: ``< > &``. Поэтому агент должен
генерировать посты в HTML — тогда `<b>жирный</b>` отрендерится, а не покажется как текст.

Поддерживаемые Telegram HTML-теги: b/strong, i/em, u, s/del, code, pre, a href,
blockquote, tg-spoiler.
"""

from __future__ import annotations

import re

PARSE_MODE = "html"

# минимальный набор HTML-тегов, которые понимает Telegram
ALLOWED_TAGS = ("b", "strong", "i", "em", "u", "s", "del", "code", "pre", "a", "blockquote", "tg-spoiler")


def escape_html(text: str) -> str:
    """Экранировать спецсимволы HTML (только эти три нужны Telegram)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bold(text: str) -> str:
    return f"<b>{escape_html(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{escape_html(text)}</i>"


def underline(text: str) -> str:
    return f"<u>{escape_html(text)}</u>"


def strike(text: str) -> str:
    return f"<s>{escape_html(text)}</s>"


def code(text: str) -> str:
    return f"<code>{escape_html(text)}</code>"


def pre(text: str, lang: str | None = None) -> str:
    if lang:
        return f'<pre><code class="language-{lang}">{escape_html(text)}</code></pre>'
    return f"<pre>{escape_html(text)}</pre>"


def link(text: str, url: str) -> str:
    return f'<a href="{escape_html(url)}">{escape_html(text)}</a>'


def spoiler(text: str) -> str:
    return f"<tg-spoiler>{escape_html(text)}</tg-spoiler>"


def blockquote(text: str) -> str:
    return f"<blockquote>{escape_html(text)}</blockquote>"


# --- конвертация Markdown агента → Telegram HTML --------------------------------
#
# Claude отвечает обычным Markdown (**жирный**, `код`, ```блоки```, [ссылки]),
# а Telegram его не рендерит как plain-текст. Конвертируем в безопасный HTML-подмножество
# Telegram. Подчёркивания НЕ трактуем как курсив — иначе `ML_Road`/`snake_case` ломались бы.

_FENCED = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_HEADER = re.compile(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_PH = re.compile(r"\x00(\d+)\x00")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def _is_table_sep(line: str) -> bool:
    s = line.strip().strip("|")
    cells = s.split("|")
    return bool(s) and all(set(c.strip()) <= set("-: ") and "-" in c for c in cells)


def _convert_tables(text: str) -> str:
    """Markdown-таблицы → читаемые списки (Telegram таблицы не рендерит).

    | A | B |        →   • A: x · B: y
    |---|---|            • A: z
    | x | y |
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_table_row(lines[i]) and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 2  # пропустить заголовок и разделитель
            while i < len(lines) and _is_table_row(lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                pairs = []
                for k, c in enumerate(cells):
                    if not c or c == "—":
                        continue
                    key = header[k] if k < len(header) else ""
                    pairs.append(f"{key}: {c}" if key else c)
                out.append("• " + " · ".join(pairs) if pairs else "•")
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def md_to_telegram_html(md: str) -> str:
    """Перевести Markdown (как пишет Claude) в Telegram-HTML.

    Поддержка: **жирный**, `код`, ```блоки```, # заголовки→жирный, [текст](http…),
    markdown-таблицы → списки. Курсив по ``_`` намеренно НЕ трогаем (ломал бы подчёркивания).
    """
    stash: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    tmp = _FENCED.sub(_stash, md)          # вынуть блоки кода
    tmp = _INLINE_CODE.sub(_stash, tmp)    # и инлайн-код — чтобы не трогать их содержимое
    tmp = escape_html(tmp)                 # экранировать <>& в остальном тексте
    tmp = _convert_tables(tmp)             # markdown-таблицы → списки (Telegram таблицы не рендерит)
    tmp = _HEADER.sub(r"<b>\1</b>", tmp)   # # Заголовок → жирный
    tmp = _BOLD.sub(r"<b>\1</b>", tmp)     # **жирный**
    tmp = _LINK.sub(r'<a href="\2">\1</a>', tmp)

    def _restore(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        if idx >= len(stash):  # «голый» \x00N\x00 во входе (не наш плейсхолдер) — не падаем
            return ""
        raw = stash[idx]
        if raw.startswith("```"):
            inner = re.sub(r"^```\w*\n?", "", raw)
            inner = re.sub(r"\n?```$", "", inner)
            return f"<pre>{escape_html(inner)}</pre>"
        return f"<code>{escape_html(raw.strip('`'))}</code>"

    return _PH.sub(_restore, tmp)
