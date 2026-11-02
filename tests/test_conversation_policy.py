"""Applied conversation requirements influence strategy, never alter source facts."""
import copy

import pytest

from chatdock.context import CONVERSATION_POLICIES, DEFAULT_CONVERSATION_POLICY, message_cost, retrieve
from chatdock.engine import Engine
from chatdock.network import Task
from chatdock.store import Store


class RecordingClient:
    def __init__(self):
        self.calls = []
    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        task.check()
        assert message_cost(messages) <= context - 1024
        self.calls.append((copy.deepcopy(messages), reply))
        if '续聊摘要' in messages[0]['content']:
            return 'S' * 2400
        emit('回答')
        return '回答'


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'policy.db')
    client = RecordingClient()
    engine = Engine(store, client)
    engine.model = 'test:latest'
    return store, client, engine, store.new_session()


@pytest.mark.parametrize('level', range(1, 6))
def test_applied_model_receives_correct_policy_without_reallocating_context(setup, level):
    store, client, engine, session = setup
    original_context = engine.context
    store.set('model_demands', {engine.model: level})
    assert engine.conversation_policy == dict(CONVERSATION_POLICIES[level], level=level)
    assert engine.context == original_context
    store.set('speech', {'conversation_demand': 1 if level != 1 else 5})
    assert engine.conversation_policy['level'] == level  # Recommendation controls are separate.


@pytest.mark.parametrize('mapping', [None, [], {}, {'other:latest': 5}, {'test:latest': True}, {'test:latest': 0}, {'test:latest': 6}, {'test:latest': '5'}])
def test_legacy_or_invalid_mappings_preserve_previous_strategy(setup, mapping):
    store, client, engine, session = setup
    if mapping is not None:
        store.set('model_demands', mapping)
    store.set('speech', {'conversation_demand': 5})
    assert engine.conversation_policy == dict(DEFAULT_CONVERSATION_POLICY, level=None)


def test_ollama_normalized_spelling_keeps_applied_policy(setup):
    store, client, engine, session = setup
    engine.model = 'hf.co/org/model:file.gguf'
    store.set('model_demands', {'hf.co/Org/Model:File.GGUF': 4})
    assert engine.conversation_policy['level'] == 4


def populate_references(store):
    for i in range(10):
        store.add_memory(f'已确认事实{i}：' + '完整保留这段偏好' * 10, status='approved', score=10-i//2, analysis='原文说明')
    store.add_memory('PENDING_MEMORY_SECRET', status='pending', score=10)
    for i in range(6):
        store.add_knowledge(f'术语{i}', f'完整释义{i}：' + '不得从中截断成半条事实' * 8, status='approved')
    store.add_knowledge('术语秘密', 'PENDING_KNOWLEDGE_SECRET')


def test_higher_demand_can_retrieve_more_complete_approved_records(setup):
    store, client, engine, session = setup
    populate_references(store)
    engine.context = engine.model_limit = 32768
    query = '术语0 术语1 术语2 术语3 术语4 术语5'
    sizes=[]
    before_memories = store.items('memories')
    before_knowledge = store.items('knowledge')
    for level in range(1, 6):
        store.set('model_demands', {engine.model: level})
        _, _, memories, knowledge = engine.prompt_data(session, query)
        sizes.append((len(memories), len(knowledge)))
        assert 'PENDING' not in str(memories) + str(knowledge)
        for row in memories:
            assert row['summary'] in [m['summary'] for m in before_memories]
        for row in knowledge:
            assert row['definition'] in [k['definition'] for k in before_knowledge]
    assert sizes[-1][0] > sizes[0][0] and sizes[-1][1] > sizes[0][1]
    assert store.items('memories') == before_memories and store.items('knowledge') == before_knowledge


def test_optional_references_shed_before_valid_raw_message_is_rejected(setup):
    store, client, engine, session = setup
    populate_references(store)
    store.set('model_demands', {engine.model: 5})
    store.set('user_profile', '用户资料完整原文🙂')
    raw = '术语0 请保留我的完整请求：' + 'x' * 1700
    before = store.items('memories')
    _, persona, memories, knowledge = engine.prompt_data(session, raw)
    assert memories[0] == '用户自行填写的资料：用户资料完整原文🙂'
    preview = engine.preview(session, raw)
    assert not preview['too_long']
    engine.chat(session, raw, Task(), lambda *a: None)
    sent = client.calls[-1][0]
    assert sent[-1]['content'] == raw
    assert store.messages(session)[0]['content'] == raw
    assert '用户资料完整原文🙂' in sent[0]['content'] and persona in sent[0]['content']
    assert store.items('memories') == before


def test_oversized_persona_is_warned_instead_of_shortened(setup):
    store, client, engine, session = setup
    store.set('model_demands', {engine.model: 5})
    persona = '完整人格不可被截断' * 240
    store.save_persona(1, 'Large', persona)
    assert engine.preview(session, 'hello')['too_long']
    with pytest.raises(ValueError, match='超过上下文预算'):
        engine.chat(session, 'hello', Task(), lambda *a: None)
    assert not client.calls and store.persona(1)['prompt'] == persona


def test_higher_demand_summarizes_earlier_but_does_not_change_originals(setup):
    store, client, engine, session = setup
    base = engine.preview(session, '继续')['used']
    store.add_message(session, 'user', 'x' * (int(engine.budget.input_limit * .85) - base - 32))
    before = store.messages(session)
    store.set('model_demands', {engine.model: 1})
    assert not engine.preview(session, '继续')['rollover']
    store.set('model_demands', {engine.model: 5})
    assert engine.preview(session, '继续')['rollover']
    target = engine.chat(session, '继续', Task(), lambda *a: None)
    assert target != session and store.messages(session) == before
    assert len(store.session(target)['summary']) == 2000
    assert client.calls[0][1] == engine.budget.reply
    assert store.checkpoints()[0]['summary'] == store.session(target)['summary']


def test_reply_language_counts_adapted_copy_but_preserves_originals(setup):
    store, client, engine, session = setup
    store.set('response_language', 'en')
    store.set('model_demands', {engine.model: 4})
    store.set('user_profile', '中文资料 must remain as entered')
    raw = '请保持这条原文，不要改写。'
    preview = engine.preview(session, raw)
    engine.chat(session, raw, Task(), lambda *a: None)
    sent = client.calls[-1][0]
    assert 'REPLY-LANGUAGE SETTING: ENGLISH' in sent[0]['content']
    assert '中文资料 must remain as entered' in sent[0]['content']
    assert sent[-1]['content'].startswith(raw) and 'Application preference' in sent[-1]['content']
    assert store.messages(session)[0]['content'] == raw
    assert preview['used'] == message_cost(sent)


def test_retrieval_memory_budget_is_optional_and_does_not_include_candidates(setup):
    store, client, engine, session = setup
    store.add_memory('完整事实' * 20, status='approved')
    store.add_memory('PENDING', status='pending')
    assert retrieve(store, 'hello', memory_budget=0)[0] == []
    assert len(retrieve(store, 'hello')[0]) == 1
    assert len(retrieve(store, 'hello', memory_budget=3000)[0]) == 1


def test_api_model_with_same_name_does_not_inherit_local_deployment_policy(setup):
    store, client, engine, session = setup
    store.set("model_demands", {engine.model: 5})
    assert engine.conversation_policy["level"] == 5
    engine.backend = "api"
    assert engine.conversation_policy == dict(DEFAULT_CONVERSATION_POLICY, level=None)
    assert store.get("model_demands")[engine.model] == 5
    engine.backend = "local"
    assert engine.conversation_policy["level"] == 5
