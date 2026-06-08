"""Тесты HTML-форматирования для Telegram."""

from __future__ import annotations

from tgcockpit.util import formatting as f


def test_escape_html():
    assert f.escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_bold_italic_code():
    assert f.bold("Привет") == "<b>Привет</b>"
    assert f.italic("ок") == "<i>ок</i>"
    assert f.code("x=1") == "<code>x=1</code>"


def test_link_escapes():
    assert f.link("тут", "https://x.com?a=1&b=2") == '<a href="https://x.com?a=1&amp;b=2">тут</a>'


def test_escapes_inside_tags():
    # спецсимволы внутри контента экранируются, чтобы не сломать разметку
    assert f.bold("a < b") == "<b>a &lt; b</b>"
    assert f.spoiler("3 > 2") == "<tg-spoiler>3 &gt; 2</tg-spoiler>"


def test_parse_mode_is_html():
    assert f.PARSE_MODE == "html"


# --- Markdown агента → Telegram HTML ----------------------------------------


def test_md_bold():
    assert f.md_to_telegram_html("привет **мир**") == "привет <b>мир</b>"


def test_md_underscore_not_italic():
    # ML_Road / snake_case не должны превращаться в курсив
    assert f.md_to_telegram_html("канал **ML_Road**") == "канал <b>ML_Road</b>"
    assert "_" in f.md_to_telegram_html("snake_case_name")  # подчёркивания сохранены как есть


def test_md_inline_code_and_link():
    assert f.md_to_telegram_html("`x=1`") == "<code>x=1</code>"
    assert f.md_to_telegram_html("[тут](https://x.com)") == '<a href="https://x.com">тут</a>'


def test_md_escapes_html_specials():
    assert f.md_to_telegram_html("a < b & c") == "a &lt; b &amp; c"


def test_md_code_block():
    out = f.md_to_telegram_html("```\nprint(1)\n```")
    assert out.startswith("<pre>") and "print(1)" in out and out.rstrip().endswith("</pre>")


def test_md_header_to_bold():
    assert f.md_to_telegram_html("## Заголовок") == "<b>Заголовок</b>"


def test_md_table_to_list():
    table = (
        "| Статус | Запланирован | Заголовок |\n"
        "|--------|--------------|-----------|\n"
        "| draft | — | Feature Engineering |\n"
    )
    out = f.md_to_telegram_html(table)
    assert "|" not in out  # никаких сырых палок
    assert "• Статус: draft · Заголовок: Feature Engineering" in out
    assert "Запланирован" not in out  # пустые (—) ячейки выкинуты


def test_md_table_bold_inside_cells_rendered():
    table = "| A | B |\n|---|---|\n| **жирно** | x |\n"
    out = f.md_to_telegram_html(table)
    assert "<b>жирно</b>" in out and "|" not in out
