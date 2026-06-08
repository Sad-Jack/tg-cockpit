"""Общие фикстуры: изолированный временный корень репозитория на каждый тест."""

from __future__ import annotations

import pytest

from tgcockpit import paths


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Подменить корень репо на tmp_path: каждый тест работает в чистой песочнице.

    ``repo_root`` закэширован через lru_cache — чистим кэш до и после.
    """
    monkeypatch.setenv("TGCOCKPIT_HOME", str(tmp_path))
    paths.repo_root.cache_clear()
    (tmp_path / "channels").mkdir()
    (tmp_path / "secrets").mkdir()
    yield tmp_path
    paths.repo_root.cache_clear()


@pytest.fixture
def channel(repo):
    """Готовый инициализированный и ИЗУЧЕННЫЙ канал 'test' в песочнице.

    studied=True — иначе гард постинга (require_studied) заблокирует post/schedule/reply.
    Отдельный тест на сам гард создаёт неизученную сущность явно (см. test_study_guard).
    """
    from tgcockpit.config import ChannelConfig
    from tgcockpit.storage import workspace

    workspace.init_channel(
        name="test",
        handle="@test_channel",
        timezone="Europe/Moscow",
        pillars=["обучение", "кейсы"],
        default_slots=["mon 09:00"],
    )
    cfg = ChannelConfig.load("test")
    cfg.studied = True
    cfg.save()
    return "test"
