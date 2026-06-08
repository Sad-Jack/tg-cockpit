"""Тесты моста к Claude: маппинг событий SDK и сборка промпта."""

from __future__ import annotations

import pytest

from tgcockpit import bridge


class _Sys:
    def __init__(self):
        self.data = {"session_id": "sess-1"}


class _Text:
    def __init__(self, t):
        self.text = t


class _Tool:
    def __init__(self, name, inp):
        self.name = name
        self.input = inp


class _Thinking:
    def __init__(self, t):
        self.thinking = t


class _Assistant:
    def __init__(self, content):
        self.content = content


class _Result:
    def __init__(self):
        self.result = "готово"
        self.total_cost_usd = 0.02
        self.session_id = "sess-1"


# имена классов важны: classify смотрит type(message).__name__
_Sys.__name__ = "SystemMessage"
_Text.__name__ = "TextBlock"
_Tool.__name__ = "ToolUseBlock"
_Thinking.__name__ = "ThinkingBlock"
_Assistant.__name__ = "AssistantMessage"
_Result.__name__ = "ResultMessage"


def test_classify_system():
    evs = bridge.classify(_Sys())
    assert evs == [{"kind": "session", "session_id": "sess-1"}]


def test_classify_assistant_text_and_tool():
    evs = bridge.classify(_Assistant([_Text("думаю"), _Tool("Bash", {"cmd": "ls"})]))
    assert evs[0] == {"kind": "text", "text": "думаю"}
    assert evs[1]["kind"] == "tool" and evs[1]["name"] == "Bash"


def test_classify_thinking_block():
    # extended thinking → отдельное событие thinking (для консоли, не в чат)
    evs = bridge.classify(_Assistant([_Thinking("прикидываю план"), _Text("ответ")]))
    assert evs[0] == {"kind": "thinking", "text": "прикидываю план"}
    assert evs[1] == {"kind": "text", "text": "ответ"}


def test_classify_result():
    evs = bridge.classify(_Result())
    assert evs[0]["kind"] == "result"
    assert evs[0]["text"] == "готово"
    assert evs[0]["cost"] == 0.02


def test_build_prompt_with_channel():
    p = bridge.build_prompt("сделай пост", "mlchan")
    assert "mlchan" in p
    assert "сделай пост" in p
    assert "только на русском" in p.lower()  # правило языка подмешано


def test_build_prompt_without_channel():
    p = bridge.build_prompt("привет", None)
    assert "привет" in p
    assert "только на русском" in p.lower()  # даже без активной сущности — правило языка


def test_build_options_appends_russian_rule(repo, tmp_path):
    pytest.importorskip("claude_agent_sdk")
    opts = bridge._build_options(tmp_path, None)
    sp = opts.system_prompt
    assert isinstance(sp, dict) and sp.get("preset") == "claude_code"
    assert "русск" in sp.get("append", "").lower()


def test_model_effort_default(repo):
    # без secrets/.env → значения по умолчанию (None), строка для старта понятная
    m, e = bridge.model_effort()
    assert m is None and e is None
    assert "по умолчанию" in bridge.describe_model()


def test_config_effort_validation():
    from tgcockpit.config import Secrets

    assert Secrets(api_id=1, api_hash="x" * 32, effort="high").effort == "high"
    assert Secrets(api_id=1, api_hash="x" * 32, effort="").effort is None  # пусто → None
    with pytest.raises(Exception):
        Secrets(api_id=1, api_hash="x" * 32, effort="ultra")  # неизвестный уровень
