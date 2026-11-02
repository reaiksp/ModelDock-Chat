"""Real compressed-model memory/slang check using synthetic temporary data only.

Does not download models, run speech, read the user's chat database, or train
weights. All temporary private model names created here are deleted afterward.
"""
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatdock.engine import Engine
from chatdock.hardware import detect
from chatdock.network import Client, Task, model_context, model_key
from chatdock.personal_model import delete_snapshot, verify_snapshot
from chatdock.store import Store

BASE = 'hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf'
CODE = '琥珀灯塔731'
TERM = '绿灯熊'
PENDING_MEMORY = 'PENDING_MEMORY_SECRET_891'
PENDING_KNOWLEDGE = 'PENDING_KNOWLEDGE_SECRET_892'
OUTPUT = Path(__file__).resolve().parents[1] / 'artifacts/quant-memory-slang-result.json'


class ResourceUnavailable(RuntimeError):
    pass


class TrackingClient(Client):
    def __init__(self):
        super().__init__()
        self.created = []
    def request(self, path, body=None, task=None, timeout=180):
        if path == '/api/create' and body and body.get('model', '').startswith('mdc-private-'):
            self.created.append(body['model'])
        return super().request(path, body, task, timeout)


def valid_answer(answer):
    normalized = re.sub(r'\s+', '', answer)
    return CODE in normalized and '通过' in normalized and '测试' in normalized


def verify_configuration_without_inference(client, actual, report):
    """Manifest-only check. Facts here are explicitly manual test fixtures.

    Creating and reading a SYSTEM model configuration does not launch the model
    runner. This does not claim AI extraction, learning or successful recall.
    """
    with tempfile.TemporaryDirectory(prefix='mdc-quant-config-only-') as folder:
        store = Store(Path(folder) / 'synthetic.db')
        store.save_persona(1, '配置校验', '这是虚构配置校验，请简短回答。')
        store.set('user_profile', '这是手工预置的虚构测试资料。')
        memory = store.add_memory('测试代号是' + CODE + '。', status='approved', score=9,
            analysis='手工合成且已批准的测试夹具，不是 AI 提取结果。')
        term = store.add_knowledge(TERM, '已经通过测试的功能', status='approved', source='手工合成测试夹具')
        store.add_memory(PENDING_MEMORY, status='pending')
        store.add_knowledge('未确认词条', PENDING_KNOWLEDGE, status='pending')
        engine = Engine(store, client)
        engine.model = engine.local_model = actual
        engine.context = 4096
        engine.model_limit = model_context(client, actual, Task())
        session = store.new_session()
        expected = engine.personal_snapshot(session)
        model = engine.sync_personal_model(session, Task(), lambda *a: None, force=True)
        saved = client.request('/api/show', {'model': model['name']}, timeout=30)
        system = saved.get('system', '')
        assert verify_snapshot(client, model['name'], expected, Task(), 4096)
        assert CODE in system and TERM in system and '已经通过测试的功能' in system
        assert PENDING_MEMORY not in system and PENDING_KNOWLEDGE not in system
        assert memory in expected['included_ids'] and term in expected['included_knowledge_ids']
        report.update(configuration_write_and_read_verified=True,
            approved_fixture_memory_and_slang_in_saved_system=True,
            candidate_isolation=True, fixtures='manually preloaded approved synthetic records',
            ai_memory_extraction_run=False, ai_slang_learning_run=False, inference_recall_run=False,
            configuration_snapshot_fingerprint=expected['fingerprint'])
        prior = OUTPUT.with_name('quant-memory-slang-loading-attempt.json')
        if prior.exists():
            report['prior_real_loading_attempt'] = json.loads(prior.read_text('utf-8'))
        print('Combined approved fixture SYSTEM write/read verified without inference.', flush=True)


def run():
    started = time.monotonic()
    client = TrackingClient()
    report = {'status': 'running', 'base_model': BASE, 'context': 4096,
        'synthetic_temporary_database_only': True, 'model_download': False,
        'weight_training': False, 'speech_started': False, 'cleanup': []}
    before = {}
    try:
        report['ollama_version'] = client.request('/api/version', timeout=10).get('version')
        rows = client.request('/api/tags', timeout=10).get('models', [])
        before = {model_key(row['name']): row.get('digest') for row in rows}
        actual = next((row['name'] for row in rows if model_key(row['name']) == model_key(BASE)), None)
        if not actual:
            raise RuntimeError('The required compressed model is absent; no download will be attempted.')
        hardware = detect(OUTPUT.parent)
        gpu_budget = max(0, hardware.vram - max(1, hardware.vram * .12))
        ram_budget = max(0, min(hardware.ram - max(3, hardware.ram * .2), hardware.available - 1))
        report['hardware_preflight'] = {'free_vram_gib': round(hardware.vram, 2),
            'available_ram_gib': round(hardware.available, 2),
            'usable_vram_budget_gib': round(gpu_budget, 2), 'usable_ram_budget_gib': round(ram_budget, 2)}
        if gpu_budget < 10 or ram_budget < 2:
            verify_configuration_without_inference(client, actual, report)
            raise ResourceUnavailable('Current free GPU/RAM budgets do not meet this 4K IQ2_XS test plan. Only manifest write/read was tested; no application was closed or inference request sent in this run.')
        with tempfile.TemporaryDirectory(prefix='mdc-quant-memory-slang-') as folder:
            store = Store(Path(folder) / 'synthetic.db')
            store.save_persona(1, '合成验证', '你是简洁的中文助手。回答以“测试：”开头，遵循用户明确偏好，不编造未提供的事实。')
            store.set('user_profile', '这是虚构测试资料，喜欢简短中文回答。')
            store.set('model_demands', {actual: 2})
            store.add_memory(PENDING_MEMORY, status='pending', score=10)
            store.add_knowledge('未确认词条', PENDING_KNOWLEDGE, status='pending')
            engine = Engine(store, client)
            engine.model = engine.local_model = actual
            engine.context = 4096
            engine.model_limit = model_context(client, actual, Task())
            session = store.new_session(1, '压缩模型虚构资料验证')
            raw_evidence = '请记住：我的测试代号是' + CODE + '，我喜欢简洁的回答。'
            store.add_message(session, 'user', raw_evidence)
            store.add_message(session, 'assistant', '已收到，本次仅使用虚构的测试资料。')
            print('Analyzing explicit synthetic user evidence.', flush=True)
            count = engine.analyze_memory(session, Task(), lambda *a: None)
            candidates = [m for m in store.items('memories') if m['source_session'] == session]
            if not candidates:
                raise AssertionError('The quantized model produced no valid evidence-backed memory candidate.')
            assert all(m['status'] == 'pending' and m['evidence'] in raw_evidence for m in candidates)
            store.approve('memories', [m['id'] for m in candidates])
            report['memory_candidates'] = len(candidates)
            report['memory_evidence_checked'] = True
            material = '合成测试材料：新词“' + TERM + '”的定义是“已经通过测试的功能”。例句：这个功能是' + TERM + '。只依据这段材料理解此词。'
            print('Learning synthetic slang in an isolated request.', flush=True)
            learned = engine.learn(material, Task(), lambda *a: None)
            vocabulary = [k for k in store.items('knowledge') if k['term'] == TERM]
            if not vocabulary:
                raise AssertionError('The quantized model did not extract the defined synthetic term.')
            assert all(k['status'] == 'pending' and '通过' in k['definition'] and '测试' in k['definition'] for k in vocabulary)
            store.approve('knowledge', [k['id'] for k in vocabulary])
            report['slang_learning'] = learned
            report['learned_terms'] = [{k: r[k] for k in ['term', 'definition']} for r in vocabulary]
            question = '我的测试代号是什么？' + TERM + '是什么意思？只用一句话回答。'
            original_evidence = store.messages(session)
            engine.chat(session, question, Task(), lambda *a: None)
            answer = store.messages(session)[-1]['content']
            report['retrieved_chat_answer'] = answer
            assert valid_answer(answer), 'The model did not correctly answer both synthetic facts.'
            assert store.messages(session)[0]['content'] == raw_evidence
            assert store.messages(session)[-2]['content'] == question
            report['live_memory_and_slang_recall'] = True
            report['raw_input_preserved'] = True

            # Trigger a short synthetic continuation without altering the source.
            continuation_question = question
            for i in range(12):
                if engine.preview(session, continuation_question)['rollover']:
                    break
                store.add_message(session, 'user', f'合成任务第{i+1}轮：核对文字、资料和计划，保持原始记录。' * 2)
                store.add_message(session, 'assistant', f'合成计划第{i+1}轮：先验证资料，再核对摘要，不新增用户事实。' * 2)
            source_rows = store.messages(session)
            assert engine.preview(session, continuation_question)['rollover']
            target = engine.chat(session, continuation_question, Task(), lambda *a: None)
            assert target != session and store.messages(session) == source_rows
            assert store.checkpoints(session) and store.session(target)['summary']
            continuation_answer = store.messages(target)[-1]['content']
            report['continuation_answer'] = continuation_answer
            assert valid_answer(continuation_answer), 'Synthetic memory/slang did not survive the tested continuation.'
            report['continuation_with_checkpoint'] = True

            print('Persisting a combined memory/vocabulary SYSTEM snapshot.', flush=True)
            snapshot = engine.sync_personal_model(target, Task(), lambda *a: None, force=True)
            prompt = engine.personal_snapshot(target)
            info = client.request('/api/show', {'model': snapshot['name']}, timeout=30)
            system = info.get('system', '')
            assert verify_snapshot(client, snapshot['name'], prompt, Task(), 4096)
            assert CODE in system and TERM in system
            assert PENDING_MEMORY not in system and PENDING_KNOWLEDGE not in system
            assert prompt.get('included_knowledge_ids'), 'Combined snapshot did not include approved vocabulary.'
            default_answer = client.chat(snapshot['name'], [{'role': 'user', 'content': question}], Task(), lambda _: None, 4096, 256)
            report['private_model_default_system_answer'] = default_answer
            assert valid_answer(default_answer), 'Saved default SYSTEM did not recall both synthetic facts.'
            report['combined_default_system_recall'] = True
            report['candidate_isolation'] = True
            report['approved_memories'] = len(store.items('memories', 'approved'))
            report['approved_vocabulary'] = len(store.items('knowledge', 'approved'))
            assert all(m['content'] == raw_evidence for m in original_evidence if m['role'] == 'user')
        report['status'] = 'passed'
    except ResourceUnavailable as exc:
        report.update(status='configuration_verified_inference_skipped_resources', inference_skip_reason=str(exc))
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
    finally:
        for name in reversed(list(dict.fromkeys(client.created))):
            if model_key(name) in before:
                report['cleanup'].append({'name': name, 'removed': False, 'error': 'Pre-existing model was not touched.'})
                report['status'] = 'failed'
                continue
            try:
                delete_snapshot(client, name, Task())
                report['cleanup'].append({'name': name, 'removed': True})
            except Exception as exc:
                report['cleanup'].append({'name': name, 'removed': False, 'error': str(exc)})
                report['status'] = 'failed'
        try:
            after = {model_key(r['name']): r.get('digest') for r in client.request('/api/tags', timeout=10).get('models', [])}
            report['base_preserved'] = before.get(model_key(BASE)) is not None and after.get(model_key(BASE)) == before[model_key(BASE)]
            report['all_test_models_removed'] = all(model_key(n) not in after for n in client.created)
            if not report['base_preserved'] or not report['all_test_models_removed']:
                report['status'] = 'failed'
        except Exception as exc:
            report.update(status='failed', inventory_error=str(exc))
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'report': str(OUTPUT), 'elapsed_seconds': report['elapsed_seconds'],
            'error': report.get('error'), 'all_test_models_removed': report.get('all_test_models_removed')}, ensure_ascii=False), flush=True)
    return 0 if report['status'] in {'passed', 'configuration_verified_inference_skipped_resources'} else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(run())
