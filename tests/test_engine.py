import json

import pytest

from chatdock.engine import Engine
from chatdock.context import message_cost
from chatdock.network import Cancelled, Task
from chatdock.store import Store


class FakeClient:
    def __init__(self):
        self.calls = []
        self.stop = False
        self.fail = False

    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        self.calls.append(messages)
        assert message_cost(messages) <= context - 256
        if self.fail:
            emit("partial")
            raise RuntimeError("test failure")
        if json_mode:
            if "memories" in messages[0]["content"]:
                return json.dumps({"memories": [
                    {"content": "喜欢简洁", "evidence": "我喜欢简洁", "kind": "偏好"},
                    {"content": "编造", "evidence": "不存在的证据"}]}, ensure_ascii=False)
            return json.dumps({"entries": [{"term": "YYDS", "definition": "永远的神", "example": "真是YYDS", "aliases": "yyds"}]}, ensure_ascii=False)
        if "续聊摘要" in messages[0]["content"]:
            return "用户想继续讨论。"
        emit("你好")
        return "你好"


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "chat.db")
    client = FakeClient()
    engine = Engine(store, client)
    engine.model = "test"
    return store, client, engine, store.new_session()


def test_oversized_single_prompt_never_calls_model_or_saves_message(setup):
    store, client, engine, session = setup
    text = "中" * 2000
    assert engine.preview(session, text)["too_long"]
    with pytest.raises(ValueError, match="超过上下文预算"):
        engine.chat(session, text, Task(), lambda *a: None)
    assert not client.calls
    assert not store.messages(session)


def test_context_rollover_keeps_original_and_links_continuation(setup):
    store, client, engine, session = setup
    for _ in range(8):
        store.add_message(session, "user", "历史问题" * 50)
        store.add_message(session, "assistant", "历史回答" * 50)
    assert engine.preview(session, "继续")["rollover"]
    events = []
    next_id = engine.chat(session, "继续", Task(), lambda *args: events.append(args))
    assert next_id != session
    assert store.session(next_id)["parent_id"] == session
    assert store.session(next_id)["summary"]
    assert len(store.messages(session)) == 16
    assert store.messages(next_id)[-1]["content"] == "你好"


def test_partial_failure_is_saved_but_excluded_from_next_context(setup):
    store, client, engine, session = setup
    client.fail = True
    with pytest.raises(RuntimeError):
        engine.chat(session, "hello", Task(), lambda *a: None)
    rows = store.messages(session)
    assert [r["status"] for r in rows] == ["error", "error"]
    assert rows[-1]["content"] == "partial"
    assert engine.history(session) == []


def test_memory_only_accepts_real_user_evidence(setup):
    store, client, engine, session = setup
    store.add_message(session, "user", "我喜欢简洁")
    store.add_message(session, "assistant", "你是某个身份")
    engine.analyze_memory(session, Task(), lambda *a: None)
    records = store.items("memories")
    assert len(records) == 1
    assert records[0]["status"] == "pending"
    assert records[0]["evidence"] == "我喜欢简洁"
    assert "你是某个身份" not in str(client.calls)


def test_slang_learning_is_chunked_and_isolated_from_main_chat(setup):
    store, client, engine, session = setup
    store.add_message(session, "user", "PRIVATE CHAT")
    engine.context = 2048
    result = engine.learn("YYDS 永远的神。" * 100, Task(), lambda *a: None)
    assert result["sessions"] > 1
    assert len(store.items("learning")) == result["sessions"]
    assert "PRIVATE CHAT" not in str(client.calls)
    assert store.items("knowledge")[0]["status"] == "pending"


def test_approved_knowledge_is_used_next_turn(setup):
    store, client, engine, session = setup
    ident = store.add_knowledge("YYDS", "永远的神")
    store.approve("knowledge", [ident])
    engine.chat(session, "YYDS 是什么", Task(), lambda *a: None)
    assert "永远的神" in client.calls[-1][0]["content"]


def test_variant_has_knowledge_but_no_private_memories(setup):
    store, client, engine, session = setup
    store.add_memory("PRIVATE FACT", status="approved")
    store.set("user_profile", "PRIVATE PROFILE")
    store.add_knowledge("YYDS", "永远的神", status="approved")
    prompt = engine.variant_prompt(1)
    assert "永远的神" in prompt
    assert "PRIVATE" not in prompt


def test_cancelled_request_cannot_create_a_message(setup):
    store, client, engine, session = setup
    task = Task()
    task.cancel()
    with pytest.raises(Cancelled):
        engine.chat(session, "hi", task, lambda *a: None)
    assert not store.messages(session)
