"""Тесты выбора бэкенда транскрибации (без реального STT)."""

from __future__ import annotations

import pytest

from tgcockpit.audio import transcribe as t


def _ok(name):
    def backend(p, lang):
        return f"[{name}]"

    return backend


def _fail(name):
    def backend(p, lang):
        raise t.TranscriptionError(f"{name} недоступен")

    return backend


def test_fallback_to_whisper(monkeypatch, tmp_path):
    f = tmp_path / "a.ogg"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        t, "_BACKENDS", {"shortcut": _fail("shortcut"), "swift": _fail("swift"), "whisper": _ok("whisper")}
    )
    assert t.transcribe(f, order=("shortcut", "swift", "whisper")) == "[whisper]"


def test_first_success_wins(monkeypatch, tmp_path):
    f = tmp_path / "a.ogg"
    f.write_bytes(b"x")
    monkeypatch.setattr(t, "_BACKENDS", {"shortcut": _ok("shortcut"), "whisper": _ok("whisper")})
    assert t.transcribe(f, order=("shortcut", "whisper")) == "[shortcut]"


def test_all_fail_aggregates(monkeypatch, tmp_path):
    f = tmp_path / "a.ogg"
    f.write_bytes(b"x")
    monkeypatch.setattr(t, "_BACKENDS", {"shortcut": _fail("shortcut"), "whisper": _fail("whisper")})
    with pytest.raises(t.TranscriptionError) as ei:
        t.transcribe(f, order=("shortcut", "whisper"))
    assert "shortcut" in str(ei.value) and "whisper" in str(ei.value)


def test_missing_file():
    with pytest.raises(t.TranscriptionError, match="не найден"):
        t.transcribe("/nope/x.ogg")


def test_telegram_is_primary_backend():
    # Telegram Premium — первым в цепочке, затем Apple/Whisper
    assert t.DEFAULT_ORDER[0] == "telegram"
    assert t.DEFAULT_ORDER == ("telegram", "shortcut", "swift", "whisper")
    assert "telegram" in t._BACKENDS
