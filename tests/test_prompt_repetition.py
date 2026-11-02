"""Bounded local-only request repetition, never a context-window expansion."""
import copy

import pytest

from chatdock.context import Budget, message_cost
from chatdock.engine import Engine
from chatdock.network import Task
from chatdock.store import Store

LABEL = '[短请求复述一次，只需回答一次]'


class RecordingClient:
    def __init__(self):
        self.calls = []

    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        task.check()
        assert Budget(context).check(messages)[0]
        self.calls.append(copy.deepcopy(messages))
        if '续聊摘要' in messages[0]['content']:
            return '用户正在继续讨论任务。'
        emit('DONE')
        return 'DONE'


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'chat.db')
    client = RecordingClient()
    engine = Engine(store, client)
    engine.model = 'test'
    return store, client, engine, store.new_session()


def messages(text):
    return [{'role': 'system', 'content': 'PERSONA PRIVATE_MEMORY'},
            {'role': 'user', 'content': text}]


def test_repetition_is_off_by_default_and_returns_independent_copy(setup):
    store, client, engine, session = setup
    original = messages('短请求731')
    original[0]['metadata'] = {'nested': ['unchanged']}
    copied, applied = engine.reinforce(original, '短请求731')
    assert not applied and copied == original and copied is not original
    copied[0]['metadata']['nested'].append('modified copy')
    assert original[0]['metadata']['nested'] == ['unchanged']


def test_local_short_request_is_repeated_only_once_without_persona_or_history(setup):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    raw = '回答测试731'
    original = messages(raw)
    original.insert(1, {'role': 'assistant', 'content': 'HISTORY_ONLY'})
    before = copy.deepcopy(original)
    copied, applied = engine.reinforce(original, raw)
    assert applied and original == before
    assert copied[:-1] == original[:-1]
    assert copied[-1]['content'] == raw + '\n\n' + LABEL + '\n' + raw
    assert copied[-1]['content'].count(raw) == 2
    assert 'PERSONA' not in copied[-1]['content'] and 'HISTORY_ONLY' not in copied[-1]['content']
    twice, second_applied = engine.reinforce(copied, raw)
    assert not second_applied and twice == copied
    assert twice[-1]['content'].count(LABEL) == 1


@pytest.mark.parametrize('raw', ['', '  \n\t', 'a' * 161, '中' * 54, 'x\n' + LABEL, LABEL])
def test_ineligible_text_is_not_repeated(setup, raw):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    original = messages(raw)
    copied, applied = engine.reinforce(original, raw)
    assert not applied and copied == original


@pytest.mark.parametrize('raw', ['a' * 160, '中' * 53, '🙂' * 40])
def test_160_byte_utf8_boundary_is_allowed_without_truncation(setup, raw):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    copied, applied = engine.reinforce(messages(raw), raw)
    assert applied and copied[-1]['content'].endswith(raw)
    assert len(raw.encode('utf-8')) <= 160


def test_api_backend_never_repeats_or_changes_route(setup):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    engine.backend = 'api'
    engine.provider_name = 'Test API'
    original = messages('short request')
    assert engine.reinforce(original, 'short request') == (original, False)
    preview = engine.preview(session, 'short request')
    assert not preview['reinforced']
    engine.chat(session, 'short request', Task(), lambda *a: None)
    assert LABEL not in client.calls[-1][-1]['content']
    assert engine.backend == 'api' and engine.provider_name == 'Test API'


def test_budget_shortfall_withdraws_optional_copy_without_losing_request(setup):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    raw = 'short request'
    original = messages(raw)
    original[0]['content'] += 'x' * (engine.budget.input_limit - message_cost(original) - 1)
    assert engine.budget.check(original)[0]
    copied, applied = engine.reinforce(original, raw)
    assert not applied and copied == original
    assert engine.budget.check(copied)[0]


@pytest.mark.parametrize('last', [[], [{'role': 'assistant', 'content': 'short'}],
                                  [{'role': 'user', 'content': 'different'}]])
def test_only_matching_final_user_message_can_be_reinforced(setup, last):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    assert engine.reinforce(last, 'short') == (last, False)


def test_preview_matches_outgoing_request_and_database_keeps_raw_text(setup):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    store.set('response_language', 'en')
    raw = '这是用户完整原文🙂'
    preview = engine.preview(session, raw)
    events = []
    assert preview['reinforced'] and not preview['too_long']
    engine.chat(session, raw, Task(), lambda *a: events.append(a))
    sent = client.calls[-1]
    assert preview['used'] == message_cost(sent)
    assert preview['used'] <= preview['limit']
    assert LABEL in sent[-1]['content'] and 'Application preference' in sent[-1]['content']
    assert store.messages(session)[0]['content'] == raw
    assert any('复述一次短请求' in value for kind, value in events if kind == 'status')


def test_optional_repetition_never_causes_an_extra_rollover(setup):
    store, client, engine, session = setup
    raw = '继续731'
    initial = engine.preview(session, raw)['used']
    # Fit just below the rollover trigger; optional repetition crosses 90%.
    length = int(engine.budget.input_limit * .9) - initial - 33
    store.add_message(session, 'user', 'x' * length)
    before = engine.preview(session, raw)
    assert not before['rollover']
    store.set('repeat_short_prompt', True)
    preview = engine.preview(session, raw)
    assert preview['reinforced'] and preview['used'] >= preview['limit'] * .9
    assert not preview['rollover'] and preview['single'] == before['single']
    assert preview['too_long'] == before['too_long']
    next_id = engine.chat(session, raw, Task(), lambda *a: None)
    assert next_id == session and not store.checkpoints()
    assert len(client.calls) == 1
    assert message_cost(client.calls[0]) == preview['used']


def test_after_required_rollover_only_final_chat_is_reinforced(setup):
    store, client, engine, session = setup
    store.set('repeat_short_prompt', True)
    for _ in range(6):
        store.add_message(session, 'user', '历史问题' * 40)
        store.add_message(session, 'assistant', '历史回答' * 40)
    assert engine.preview(session, '继续')['rollover']
    next_id = engine.chat(session, '继续', Task(), lambda *a: None)
    assert next_id != session and store.checkpoints()
    assert LABEL not in str(client.calls[:-1])
    assert client.calls[-1][-1]['content'].count(LABEL) == 1
    assert store.messages(next_id)[0]['content'] == '继续'
