import pytest

from chatdock.context import Budget, clip, estimate, message_cost, retrieve, split_text
from chatdock.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "chat.db")


def test_multibyte_split_preserves_text_and_budget():
    text = "中文😀abc" * 500
    chunks = split_text(text, 101)
    assert "".join(chunks) == text
    assert all(estimate(chunk) <= 101 for chunk in chunks)
    assert estimate(clip(text, 100)) <= 100


def test_context_limit_reserves_reply_and_safety():
    budget = Budget(2048, reply=768, safety=256)
    assert budget.input_limit == 1024
    with pytest.raises(ValueError, match="超过上下文预算"):
        budget.require([{"role": "user", "content": "中" * 500}])
    budget.require([{"role": "user", "content": "你好"}])


def test_knowledge_only_enters_chat_after_approval(store):
    ident = store.add_knowledge("YYDS", "永远的神", aliases="yyds")
    assert retrieve(store, "yyds")[1] == []
    store.approve("knowledge", [ident])
    assert retrieve(store, "这波 YYDS")[1][0]["definition"] == "永远的神"
    assert retrieve(store, "其他话题")[1] == []


def test_memory_candidate_requires_approval_and_deduplicates(store):
    a = store.add_memory("喜欢简洁回答")
    b = store.add_memory("喜欢简洁回答")
    assert a == b
    assert retrieve(store, "hello")[0] == []
    store.approve("memories", [a])
    assert retrieve(store, "hello")[0] == [{"score": 5, "summary": "喜欢简洁回答", "analysis": ""}]


def test_import_validates_entire_file_before_writing(store):
    data = {"format": "modeldock-chat-knowledge-v1", "entries": [
        {"term": "a", "definition": "b"}, {"term": "broken"}]}
    with pytest.raises(ValueError):
        store.import_knowledge(data)
    assert store.items("knowledge") == []


def test_export_omits_private_data_and_pending_records(store):
    store.add_memory("private", status="approved")
    store.add_knowledge("pending", "secret")
    store.add_knowledge("public", "term", status="approved")
    export = store.export_knowledge()
    assert len(export["entries"]) == 1
    assert export["entries"][0]["term"] == "public"
    assert "private" not in str(export)


def test_session_delete_cascades_messages_but_preserves_memory(store):
    ident = store.new_session()
    store.add_message(ident, "user", "hello")
    store.add_memory("fact", source_session=ident)
    store.delete_session(ident)
    assert not store.messages(ident)
    assert len(store.items("memories")) == 1


def test_retrieval_respects_size_caps(store):
    store.add_knowledge("大词条", "字" * 1000, status="approved")
    assert retrieve(store, "大词条", total_budget=100)[1] == []
