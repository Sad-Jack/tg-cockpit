"""Тесты хранилища: атомарный JSON, рабочее пространство канала, черновики."""

from __future__ import annotations

import pytest

from tgcockpit import paths
from tgcockpit.config import ChannelConfig
from tgcockpit.storage import drafts as drafts_mod
from tgcockpit.storage import jsonstore, workspace


def test_jsonstore_roundtrip(repo):
    p = repo / "channels" / "x" / "data" / "f.json"
    data = {"привет": [1, 2, 3], "nested": {"x": True}}
    jsonstore.write_json(p, data)
    assert jsonstore.read_json(p) == data
    # не-ASCII сохраняется как есть (ensure_ascii=False)
    assert "привет" in p.read_text(encoding="utf-8")


def test_jsonstore_missing_returns_default(repo):
    assert jsonstore.read_json(repo / "nope.json", default={"d": 1}) == {"d": 1}


def test_init_channel_creates_skeleton(repo):
    cfg = workspace.init_channel("ch", handle="@ch", pillars=["a"])
    assert isinstance(cfg, ChannelConfig)
    base = paths.channel_dir("ch")
    assert (base / "config.yaml").exists()
    assert (base / "CLAUDE.md").exists()
    assert (base / "content-plan.md").exists()
    for sub in ("data", "drafts", "insights"):
        assert (base / sub).is_dir()


def test_init_channel_idempotent_guard(repo):
    workspace.init_channel("ch", handle="@ch")
    with pytest.raises(FileExistsError):
        workspace.init_channel("ch", handle="@ch")
    # overwrite разрешён
    workspace.init_channel("ch", handle="@ch2", overwrite=True)
    assert ChannelConfig.load("ch").handle == "@ch2"


def test_channel_config_load_missing(repo):
    with pytest.raises(FileNotFoundError):
        ChannelConfig.load("ghost")


def test_channel_name_blocks_path_traversal(repo):
    from tgcockpit.config import validate_channel_name

    assert validate_channel_name("ML_Road") == "ML_Road"
    for bad in ("../../etc", "a/b", "..", "x/../y"):
        with pytest.raises(ValueError):
            validate_channel_name(bad)
    # ChannelConfig.load тоже блокирует traversal-имя
    with pytest.raises(ValueError):
        ChannelConfig.load("../../etc")


def test_read_json_corrupted_returns_default(repo):
    p = repo / "broken.json"
    p.write_text("{ это не json", encoding="utf-8")
    assert jsonstore.read_json(p, default={"ok": 1}) == {"ok": 1}  # не падаем


def test_init_channel_rejects_too_long_name(repo):
    # кириллица = 2 байта/символ; 40 симв = 80 байт > 60 → callback_data Telegram не влезет
    with pytest.raises(ValueError, match="[Сс]лишком длинное"):
        workspace.init_channel("я" * 40, handle="@x")


def test_draft_frontmatter_roundtrip(channel):
    d = drafts_mod.new_draft(channel, title="Привет мир", pillar="обучение", body="Тело")
    from pathlib import Path

    again = drafts_mod.load(Path(d.path))
    assert again.title == "Привет мир"
    assert again.pillar == "обучение"
    assert again.status == "draft"
    assert again.body == "Тело"
    assert again.scheduled_msg_id is None


def test_draft_unique_names(channel):
    d1 = drafts_mod.new_draft(channel, title="Тема", today="2026-06-07")
    d2 = drafts_mod.new_draft(channel, title="Тема", today="2026-06-07")
    assert d1.path != d2.path  # коллизия имён разруливается суффиксом


def test_draft_parse_without_frontmatter():
    d = drafts_mod.parse("просто текст без шапки")
    assert d.body == "просто текст без шапки"
    assert d.status == "draft"


def test_draft_parse_non_dict_frontmatter():
    # frontmatter, который yaml парсит не в dict (список/скаляр/пусто), не должен падать
    for fm in ("- a\n- b", "просто строка", ""):
        d = drafts_mod.parse(f"---\n{fm}\n---\nтело")
        assert d.body == "тело"
        assert d.status == "draft"  # дефолты применились, без AttributeError


def test_resolve_in_channel_accepts_forms(channel):
    from pathlib import Path

    d = drafts_mod.new_draft(channel, title="Пост про X", body="тело")
    name = Path(d.path).name
    # абсолютный путь, голое имя файла, относительный от корня репо — все валидны
    assert drafts_mod.resolve_in_channel(channel, d.path).name == name
    assert drafts_mod.resolve_in_channel(channel, name).name == name
    rel = str(Path(d.path).relative_to(paths.repo_root()))
    assert drafts_mod.resolve_in_channel(channel, rel).name == name


def test_resolve_in_channel_rejects_outside_and_missing(channel):
    with pytest.raises(ValueError):
        drafts_mod.resolve_in_channel(channel, "/etc/passwd")  # вне drafts
    with pytest.raises(ValueError):
        drafts_mod.resolve_in_channel(channel, "нет-такого.md")  # не существует


def test_draft_save_updates_status(channel):
    from pathlib import Path

    d = drafts_mod.new_draft(channel, title="X")
    d.status = "scheduled"
    d.scheduled_msg_id = 555
    drafts_mod.save(d)
    again = drafts_mod.load(Path(d.path))
    assert again.status == "scheduled"
    assert again.scheduled_msg_id == 555
