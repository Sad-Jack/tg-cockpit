"""Тесты Obsidian-хранилища: только текст, файл по id, .obsidian, инкрементальная синхронизация."""

from __future__ import annotations

from pathlib import Path

from tgcockpit.storage import jsonstore, workspace
from tgcockpit.study import vault


def _seed(channel, posts: dict):
    jsonstore.write_json(workspace.history_file(channel), {"snapshot": {}, "posts": posts})


def test_build_vault_text_only_and_obsidian(channel):
    _seed(channel, {
        "1": {"id": 1, "date": "2026-05-01T09:00:00", "views": 1000, "reactions": 50,
              "media_kind": "text", "text": "Первый учебный пост про ML\nвторая строка"},
        "2": {"id": 2, "date": "2026-05-02T09:00:00", "media_kind": "photo", "text": ""},  # без текста → пропуск
    })
    res = vault.build_vault(channel)
    assert res["posts_written"] == 1 and res["added"] == 1 and res["skipped_non_text"] == 1

    vdir = Path(res["vault"])
    assert vdir.name == "@test_channel"
    assert (vdir / ".obsidian" / "app.json").exists()  # настоящий Obsidian-vault
    # имя файла — по id (стабильное), не по слагу
    post = vdir / "posts" / "1.md"
    assert post.exists()
    assert "Первый учебный пост про ML" in post.read_text(encoding="utf-8")
    assert not list((vdir / "posts").glob("1-*.md"))  # нет слаг-имён
    index = (vdir / "index.md").read_text(encoding="utf-8")
    assert "[[posts/1|" in index  # вики-ссылка Obsidian


def test_vault_incremental_sync(channel):
    _seed(channel, {
        "1": {"id": 1, "date": "2026-05-01T09:00:00", "text": "пост один"},
        "2": {"id": 2, "date": "2026-05-02T09:00:00", "text": "пост два"},
    })
    r1 = vault.build_vault(channel)
    assert r1["added"] == 2

    # повторный билд без изменений → ничего не трогаем
    r2 = vault.build_vault(channel)
    assert r2["added"] == 0 and r2["updated"] == 0 and r2["unchanged"] == 2 and r2["deleted"] == 0

    # пост 1 отредактирован, пост 2 удалён из канала, добавлен пост 3
    _seed(channel, {
        "1": {"id": 1, "date": "2026-05-01T09:00:00", "text": "пост один ИЗМЕНЁН"},
        "3": {"id": 3, "date": "2026-05-03T09:00:00", "text": "пост три"},
    })
    r3 = vault.build_vault(channel)
    assert r3["updated"] == 1  # пост 1
    assert r3["added"] == 1    # пост 3
    assert r3["deleted"] == 1  # пост 2

    vdir = Path(r3["vault"])
    assert not (vdir / "posts" / "2.md").exists()           # удалённый убран
    assert (vdir / "posts" / "3.md").exists()               # новый добавлен
    assert "ИЗМЕНЁН" in (vdir / "posts" / "1.md").read_text(encoding="utf-8")  # правка применена


def test_vault_metrics_drift_not_treated_as_edit(channel):
    # метрики (views/reactions) меняются между фетчами — это НЕ правка поста
    _seed(channel, {"1": {"id": 1, "date": "2026-05-01T09:00:00", "views": 100, "text": "пост"}})
    vault.build_vault(channel)
    _seed(channel, {"1": {"id": 1, "date": "2026-05-01T09:00:00", "views": 9999, "text": "пост"}})
    r = vault.build_vault(channel)
    assert r["updated"] == 0 and r["unchanged"] == 1  # текст тот же → не перезаписываем


def test_vault_unchanged_not_rewritten(channel):
    # неизменённые посты не должны переписываться на диск (экономия I/O на больших каналах)
    _seed(channel, {"1": {"id": 1, "date": "2026-05-01T09:00:00", "text": "стабильный пост"}})
    r1 = vault.build_vault(channel)
    post = Path(r1["vault"]) / "posts" / "1.md"
    mtime_before = post.stat().st_mtime_ns
    # второй билд без изменений + манифест на месте
    r2 = vault.build_vault(channel)
    assert r2["unchanged"] == 1 and r2["updated"] == 0
    assert post.stat().st_mtime_ns == mtime_before  # файл не трогали
    assert (Path(r1["vault"]) / "posts" / ".sync.json").exists()  # манифест хэшей


def test_safe_handle_numeric_id(repo):
    workspace.init_channel("num", handle="-1001234567890", kind="supergroup")
    _seed("num", {"1": {"id": 1, "text": "сообщение", "date": "2026-05-01T10:00:00"}})
    res = vault.build_vault("num")
    assert Path(res["vault"]).name.startswith("id")
