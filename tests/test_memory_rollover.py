"""Regression coverage for automatic checkpoints and private model lifecycle."""
import copy

import pytest

from chatdock.context import message_cost
from chatdock.engine import Engine
from chatdock.network import Cancelled, Task
from chatdock.store import Store


class ModelClient:
    base = 'http://127.0.0.1:11434'

    def __init__(self):
        self.models = {'test:latest': {'model_info': {'test.context_length': 8192}}}
        self.chats = []
        self.requests = []
        self.summary_result = '用户喜欢简洁回答，正在讨论计划。'
        self.fail_summary = False
        self.cancel_summary = False

    def request(self, path, body=None, task=None, timeout=180):
        if task:
            task.check()
        self.requests.append((path, copy.deepcopy(body)))
        if path == '/api/tags':
            return {'models': [{'name': n} for n in self.models]}
        if path == '/api/show':
            name = body['model']
            name = name if ':' in name else name + ':latest'
            if name not in self.models:
                raise RuntimeError('missing model')
            return copy.deepcopy(self.models[name])
        if path == '/api/create':
            self.models[body['model'] + ':latest'] = {'system': body['system'],
                'parameters': 'num_ctx ' + str(body['parameters']['num_ctx'])}
            return {'status': 'success'}
        raise AssertionError(path)

    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        task.check()
        assert message_cost(messages) <= context - 768 - 256
        self.chats.append(copy.deepcopy(messages))
        if '续聊摘要' in messages[0]['content']:
            if self.fail_summary:
                raise RuntimeError('summary failed')
            if self.cancel_summary:
                task.cancel()
                task.check()
            return self.summary_result
        emit('答案')
        return '答案'


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'chat.db')
    client = ModelClient()
    engine = Engine(store, client)
    engine.model = 'test:latest'
    return store, client, engine, store.new_session()


def fill_to_threshold(store, engine, session, ratio=.93):
    used = engine.preview(session, '继续')['used']
    length = int(engine.budget.input_limit * ratio) - used - 32
    store.add_message(session, 'user', 'a' * max(1, length))


def test_proactive_summary_happens_before_input_is_full(setup):
    store, client, engine, session = setup
    fill_to_threshold(store, engine, session)
    preview = engine.preview(session, '继续')
    assert preview['rollover'] and preview['used'] < preview['limit']
    before = store.messages(session)
    next_id = engine.chat(session, '继续', Task(), lambda *a: None)
    assert next_id != session
    assert store.messages(session) == before
    checkpoint = store.checkpoints()[0]
    assert checkpoint['source_session'] == session and checkpoint['target_session'] == next_id
    assert checkpoint['through_message'] == before[-1]['id']
    assert checkpoint['summary'] == store.session(next_id)['summary']
    assert '不视为用户确认事实' in client.chats[-1][0]['content']
    assert not store.items('memories')


@pytest.mark.parametrize('mode', ['failure', 'empty', 'cancel'])
def test_summary_failure_preserves_original_without_checkpoint(setup, mode):
    store, client, engine, session = setup
    fill_to_threshold(store, engine, session)
    before = store.messages(session)
    if mode == 'failure':
        client.fail_summary = True
    elif mode == 'empty':
        client.summary_result = ' '
    else:
        client.cancel_summary = True
    with pytest.raises((RuntimeError, Cancelled)):
        engine.chat(session, '继续', Task(), lambda *a: None)
    assert store.messages(session) == before
    assert not store.checkpoints() and len(store.sessions()) == 1


def test_low_context_usage_does_not_summarize(setup):
    store, client, engine, session = setup
    store.add_message(session, 'user', 'hello')
    assert not engine.preview(session, '继续')['rollover']
    assert engine.chat(session, '继续', Task(), lambda *a: None) == session
    assert not store.checkpoints()


def test_api_continuation_preserves_route_and_never_creates_local_snapshot(setup):
    store, client, engine, session = setup
    engine.backend = 'api'
    engine.provider_name = 'Test API'
    store.execute('UPDATE sessions SET route_id=? WHERE id=?', ('api-test', session))
    store.set('personal_model_auto', True)
    fill_to_threshold(store, engine, session)
    next_id = engine.chat(session, '继续', Task(), lambda *a: None)
    assert store.session(next_id)['route_id'] == 'api-test'
    assert store.checkpoints()[0]['provider'] == 'Test API'
    assert client.requests == [] and not store.personal_models()
    with pytest.raises(ValueError, match='仅支持本机'):
        engine.personal_snapshot(next_id)


def test_long_summary_requests_are_chunked_with_language_preference(setup):
    store, client, engine, session = setup
    store.set('response_language', 'en')
    raw = '用户原文🙂' * 900
    store.add_message(session, 'user', raw)
    next_id = engine.chat(session, 'continue', Task(), lambda *a: None)
    assert next_id != session
    assert len(client.chats) > 3
    assert 'REPLY-LANGUAGE SETTING: ENGLISH' in client.chats[0][0]['content']
    assert store.messages(session)[0]['content'] == raw


def test_snapshot_updates_only_when_material_changes_and_is_read_back(setup):
    store, client, engine, session = setup
    good = store.add_memory('喜欢简洁', status='approved')
    store.add_memory('PENDING SECRET')
    first = engine.sync_personal_model(session, Task(), lambda *a: None)
    second = engine.sync_personal_model(session, Task(), lambda *a: None)
    assert first['name'] == second['name']
    assert len(store.personal_models()) == 1
    assert client.requests[-1][0] == '/api/show'
    system = client.models[first['name'] + ':latest']['system']
    assert '喜欢简洁' in system and 'PENDING SECRET' not in system
    store.delete_items('memories', [good])
    third = engine.sync_personal_model(session, Task(), lambda *a: None)
    assert third['name'] != first['name']
    assert '喜欢简洁' not in client.models[third['name'] + ':latest']['system']
    assert '喜欢简洁' in system  # Old snapshots are explicit separate copies.


def test_deleted_external_snapshot_is_recreated(setup):
    store, client, engine, session = setup
    first = engine.sync_personal_model(session, Task(), lambda *a: None)
    del client.models[first['name'] + ':latest']
    second = engine.sync_personal_model(session, Task(), lambda *a: None)
    assert first['name'] != second['name']
    assert second['name'] + ':latest' in client.models


def test_overwritten_external_snapshot_is_not_trusted(setup):
    store, client, engine, session = setup
    first = engine.sync_personal_model(session, Task(), lambda *a: None)
    client.models[first['name'] + ':latest']['system'] = 'altered elsewhere'
    second = engine.sync_personal_model(session, Task(), lambda *a: None)
    assert first['name'] != second['name']


def test_snapshot_failure_does_not_prevent_chat_or_replace_valid_version(setup, monkeypatch):
    import chatdock.personal_model as module
    store, client, engine, session = setup
    first = engine.sync_personal_model(session, Task(), lambda *a: None)
    store.set('personal_model_auto', True)
    store.set('user_profile', '新的用户偏好')
    monkeypatch.setattr(module, 'sync_snapshot', lambda *a: (_ for _ in ()).throw(RuntimeError('failed create')))
    assert engine.chat(session, 'hello', Task(), lambda *a: None) == session
    assert store.messages(session)[-1]['content'] == '答案'
    assert store.personal_models()[0]['name'] == first['name']
    assert store.get('personal_model_error')


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_cancellation_after_create_cleans_or_records_unverified_revision(setup, monkeypatch, cleanup_fails):
    import chatdock.personal_model as module
    store, client, engine, session = setup
    deleted = []
    def create(client, base, name, snapshot, task, context):
        task.cancel()
        return name
    def delete(client, name, task):
        assert not task.event.is_set()
        deleted.append(name)
        if cleanup_fails:
            raise RuntimeError('cleanup failed')
    monkeypatch.setattr(module, 'sync_snapshot', create)
    monkeypatch.setattr(module, 'delete_snapshot', delete)
    with pytest.raises(Cancelled):
        engine.sync_personal_model(session, Task(), lambda *a: None)
    assert len(deleted) == 1
    rows = store.personal_models()
    assert len(rows) == int(cleanup_fails)
    if rows:
        assert rows[0]['manifest']['verified'] is False


def test_continuation_snapshot_contains_checkpoint_but_not_pending_memory(setup):
    store, client, engine, session = setup
    store.add_memory('UNCONFIRMED SECRET')
    store.set('personal_model_auto', True)
    fill_to_threshold(store, engine, session)
    next_id = engine.chat(session, '继续', Task(), lambda *a: None)
    row = store.personal_models()[0]
    assert row['source_session'] == next_id and row['manifest']['summary_included']
    system = client.models[row['name'] + ':latest']['system']
    assert store.session(next_id)['summary'] in system
    assert 'UNCONFIRMED SECRET' not in system


def test_managed_derivative_resolves_to_base_and_keeps_live_chat_data(setup):
    store, client, engine, session = setup
    store.set('user_profile', 'OLD PROFILE')
    row = engine.sync_personal_model(session, Task(), lambda *a: None)
    engine.model = row['name'] + ':latest'
    assert engine.personal_base() == 'test:latest'
    store.set('user_profile', 'NEW PROFILE')
    engine.chat(session, 'hello', Task(), lambda *a: None)
    system = client.chats[-1][0]['content']
    assert 'NEW PROFILE' in system and 'OLD PROFILE' not in system


def test_delete_rejects_base_and_unknown_names(setup):
    store, client, engine, session = setup
    with pytest.raises(ValueError, match='只能删除'):
        engine.delete_personal_model('test:latest', Task())
    with pytest.raises(ValueError, match='只能删除'):
        engine.delete_personal_model('mdc-private-not-owned', Task())


def test_checkpoint_transaction_rejects_empty_and_migration_preserves_data(setup):
    store, client, engine, session = setup
    store.add_message(session, 'user', 'RAW')
    with pytest.raises(ValueError, match='摘要为空'):
        store.continue_session(session, '', 'test', 'local')
    target = store.continue_session(session, 'summary', 'test', 'local')
    migrated = Store(store.path)
    assert len(migrated.checkpoints()) == 1
    assert migrated.messages(session)[0]['content'] == 'RAW'
    migrated.delete_session(target)
    assert not migrated.checkpoints()
    assert migrated.messages(session)[0]['content'] == 'RAW'
