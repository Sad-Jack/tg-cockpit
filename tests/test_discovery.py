"""Тесты авто-обнаружения сущностей: qualify (права/типы), имена."""

from __future__ import annotations

from types import SimpleNamespace

from tgcockpit.telegram import discovery


def _rights(**kw):
    base = dict(post_messages=False, edit_messages=False, delete_messages=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_kind_detection():
    assert discovery._kind(SimpleNamespace(broadcast=True, megagroup=False)) == "channel"
    assert discovery._kind(SimpleNamespace(broadcast=False, megagroup=True)) == "supergroup"
    assert discovery._kind(SimpleNamespace(title="g")) == "group"  # Chat: нет broadcast/megagroup


def test_handle_username_or_id():
    assert discovery._handle(SimpleNamespace(username="ch", id=1)) == "@ch"
    assert discovery._handle(SimpleNamespace(username=None, id=42)) == "42"


def test_rights_missing_creator_has_all():
    assert discovery.rights_missing("channel", True, None) == []


def test_rights_missing_channel_admin_partial():
    ar = _rights(post_messages=True, delete_messages=True)  # нет edit
    assert discovery.rights_missing("channel", False, ar) == ["edit_messages"]


def test_rights_missing_supergroup_admin_ok():
    ar = _rights(delete_messages=True)
    assert discovery.rights_missing("supergroup", False, ar) == []


def test_rights_missing_no_admin_rights():
    assert set(discovery.rights_missing("channel", False, None)) == {
        "post_messages", "edit_messages", "delete_messages"
    }


def test_suggest_name():
    assert discovery.suggest_name("@MLChan", "X") == "MLChan"
    assert discovery.suggest_name("-1001234", "Моя Группа").startswith("моя")


def test_suggest_name_byte_capped():
    # длинный кириллический заголовок → имя усечено по байтам под callback_data
    name = discovery.suggest_name("-100999", "очень длинное название группы " * 5)
    assert len(name.encode("utf-8")) <= 50


def test_unique_name(repo):
    from tgcockpit.storage import workspace

    workspace.init_channel("ch", handle="@ch")
    assert discovery.unique_name("ch") == "ch-2"
    assert discovery.unique_name("free") == "free"


def test_existing_handles(repo):
    from tgcockpit.storage import workspace

    workspace.init_channel("a", handle="@MyChan")
    assert "mychan" in discovery.existing_handles()
