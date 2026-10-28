import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chatdock.context import retrieve
from chatdock.engine import Engine
from chatdock.network import OpenAIClient, Task, Cancelled
from chatdock.providers import load_providers, save_providers, validate_provider
from chatdock.store import Store


@pytest.fixture
def api():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.received.append((self.path, body, self.headers.get('Authorization')))
            model = body['model']
            self.send_response(200)
            stream = body.get('stream') and model != 'json-fallback'
            self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
            self.end_headers()
            if stream:
                frames = [': keepalive\n\n', 'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n',
                          'data: {"choices":\ndata: [{"index":0,"delta":{"content":"你"}}]}\n\n',
                          'data: {"choices":[{"index":0,"delta":{"content":"好"}}]}\n\n']
                if model == 'error':
                    frames.append('data: {"error":{"message":"SECRET_KEY"}}\n\n')
                elif model not in {'truncated', 'slow'}:
                    frames += ['data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
                               'data: {"choices":[],"usage":{"total_tokens":20}}\n\n', 'data: [DONE]\n\n']
                for frame in frames:
                    self.wfile.write(frame.encode())
                self.wfile.flush()
                if model == 'slow':
                    threading.Event().wait(2)
                return
            messages = body['messages']
            if 'memories' in messages[0]['content']:
                content = json.dumps({'memories':[{'score':8,'summary':'喜欢简洁回答','analysis':'明确偏好，可调整回答长度','evidence':'我喜欢简洁'}]}, ensure_ascii=False)
            elif 'entries' in messages[0]['content']:
                content = json.dumps({'entries':[{'term':'YYDS','definition':'永远的神','example':'YYDS','aliases':'yyds'}]}, ensure_ascii=False)
            else:
                content = '你好'
            self.wfile.write(json.dumps({'choices':[{'message':{'content':content},'finish_reason':'length' if model == 'length' else 'stop'}]},ensure_ascii=False).encode())
    httpd = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    httpd.received=[]
    threading.Thread(target=httpd.serve_forever,daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def config(api, model='test', **kw):
    return dict({'id':'test-provider','name':'Test provider','base':f'http://127.0.0.1:{api.server_port}/v1',
                 'model':model,'context':8192,'key':'test-secret','stream':True},**kw)


def test_sse_comments_multiline_unicode_and_usage(api):
    tokens=[]
    assert OpenAIClient(config(api)).chat('test',[],Task(),tokens.append)=='你好'
    assert tokens==['你','好']
    assert api.received[0][0]=='/v1/chat/completions'
    assert api.received[0][2]=='Bearer test-secret'


def test_json_response_fallback_and_nonstream_option(api):
    for model, stream in [('json-fallback',True),('test',False)]:
        client=OpenAIClient(config(api,model,stream=stream))
        assert client.chat(model,[{'role':'user','content':'hi'}],Task(),lambda _:None)=='你好'


def test_truncated_sse_and_errors_are_not_success(api):
    for model in ['truncated','error']:
        with pytest.raises(RuntimeError) as error:
            OpenAIClient(config(api,model)).chat(model,[],Task(),lambda _:None)
        assert 'SECRET_KEY' not in str(error.value)


def test_sse_cancel_interrupts_read(api):
    task=Task()
    timers=[]
    def token(_):
        if not timers:
            timer=threading.Timer(.05,task.cancel)
            timers.append(timer)
            timer.start()
    with pytest.raises(Cancelled):
        OpenAIClient(config(api,'slow')).chat('slow',[],task,token)
    for timer in timers: timer.join()


def test_output_parameter_and_optional_reasoning_are_shared(api):
    client=OpenAIClient(config(api,stream=False,token_field='max_completion_tokens',reasoning='none'))
    client.chat('test',[{'role':'user','content':'hi'}],Task(),lambda _:None)
    body=api.received[-1][1]
    assert body['max_completion_tokens']==768
    assert body['reasoning_effort']=='none'
    assert 'max_tokens' not in body and 'options' not in body


def test_structured_output_truncation_is_rejected(api):
    with pytest.raises(RuntimeError,match='截断'):
        OpenAIClient(config(api,'length')).chat('length',[{'role':'user','content':'hi'}],Task(),lambda _:None,json_mode=True)


def test_same_provider_supports_main_memory_and_isolated_slang(api,tmp_path):
    store=Store(tmp_path/'chat.db')
    engine=Engine(store)
    engine.use_provider(config(api))
    session=store.new_session(route_id='test-provider')
    engine.chat(session,'我喜欢简洁',Task(),lambda *a:None)
    engine.analyze_memory(session,Task(),lambda *a:None)
    memory=store.items('memories')[0]
    assert memory['score']==8 and memory['summary']=='喜欢简洁回答' and memory['analysis']
    assert memory['status']=='pending'
    engine.learn('YYDS 的意思是永远的神',Task(),lambda *a:None)
    learning_call=api.received[-1][1]
    assert '我喜欢简洁' not in str(learning_call)
    assert store.items('knowledge')[0]['status']=='pending'
    store.approve('knowledge',[store.items('knowledge')[0]['id']])
    engine.chat(session,'YYDS 是什么',Task(),lambda *a:None)
    assert '永远的神' in str(api.received[-1][1]['messages'][0])
    with pytest.raises(ValueError,match='本机'):
        engine.create_variant(1,'never-create',Task())


def test_legacy_database_migration_preserves_records(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE memories(id INTEGER PRIMARY KEY,content TEXT NOT NULL,kind TEXT NOT NULL,evidence TEXT NOT NULL DEFAULT '',source_session INTEGER,status TEXT NOT NULL DEFAULT 'pending',created REAL NOT NULL); INSERT INTO memories VALUES(7,'旧记忆','manual','证据',NULL,'approved',1);")
    store=Store(path)
    row=store.items('memories')[0]
    assert row['id']==7 and row['status']=='approved' and row['evidence']=='证据'
    assert row['score']==5 and row['summary']=='旧记忆' and row['analysis']==''
    assert Store(path).items('memories')==store.items('memories')


@pytest.mark.parametrize('score',[0,11,-1,1.5,True,'8'])
def test_invalid_memory_scores_cannot_enter_database(tmp_path,score):
    store=Store(tmp_path/'chat.db')
    with pytest.raises(ValueError): store.add_memory('fact',score=score)


def test_memory_priority_and_edit_require_reapproval(tmp_path):
    store=Store(tmp_path/'chat.db')
    low=store.add_memory('低优先级',score=2,status='approved')
    high=store.add_memory('高优先级',score=9,analysis='用于后续回答',status='approved')
    assert retrieve(store,'hi')[0][0]['score']==9
    store.update_memory(high,'修改概述',10,'修改分析')
    assert all(row['summary']!='修改概述' for row in retrieve(store,'hi')[0])
    assert store.items('memories')[0]['status']=='pending'


def test_provider_metadata_drops_keys_and_migrates_old_config(tmp_path):
    store=Store(tmp_path/'chat.db')
    store.set('provider_base','https://example.com/v1')
    store.set('provider_model','model')
    rows=load_providers(store)
    assert rows[0]['id']=='legacy-learning'
    save_providers(store,[dict(rows[0],key='NEVER_STORE')])
    assert 'NEVER_STORE' not in str(store.get('providers_v2'))
    with pytest.raises(ValueError): validate_provider(dict(rows[0],id='../bad'))
