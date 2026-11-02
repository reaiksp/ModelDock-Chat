"""Audited GGUF downloads and CLI imports are bounded, verified and cancellable."""
import hashlib
import io
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from chatdock import gguf
from chatdock.network import Cancelled, Task

TAG = 'hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf'


@pytest.fixture
def small_spec(monkeypatch):
    payload = b'GGUF' + b'known synthetic bytes' * 30
    spec = dict(gguf.FILES[TAG.casefold()], size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setitem(gguf.FILES, TAG.casefold(), spec)
    return spec, payload


class Response:
    status = 200
    def __init__(self, payload, length=None, task=None, cancel_read=0):
        self.stream = io.BytesIO(payload)
        self.headers = {} if length is None else {'Content-Length': str(length)}
        self.reads = 0
        self.task, self.cancel_read = task, cancel_read
        self.sock = SimpleNamespace(shutdown=lambda mode: None)
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))
    def geturl(self):
        return 'https://us.aws.cdn.hf.co/cas-bridge-test?signature=PRIVATE_SIGNED_TOKEN'
    def read(self, n):
        self.reads += 1
        if self.task and self.cancel_read == self.reads:
            assert self.task.connection.sock is self.sock
            self.task.cancel()
        return self.stream.read(n)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stream.close()


def use_response(monkeypatch, response, seen=None):
    def open_request(request, timeout):
        if seen is not None:
            seen.append((request.full_url, timeout))
        return response
    monkeypatch.setattr(gguf.urllib.request, 'build_opener', lambda *a: SimpleNamespace(open=open_request))


def test_only_exact_audited_non_mtp_tags_are_supported():
    assert gguf.is_supported(TAG.swapcase())
    assert len(gguf.FILES) >= 7
    assert not gguf.is_supported(TAG.replace('.gguf', '-mtp.gguf'))
    assert not gguf.is_supported('hf.co/other/repo:IQ2_XS')
    assert not gguf.is_supported('https://example.com/model.gguf')
    for spec in gguf.FILES.values():
        assert len(spec['revision']) == 40 and len(spec['sha256']) == 64
        assert '-mtp' not in spec['filename'] and spec['size'] > 0


def test_unsupported_tag_never_touches_network_or_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gguf, '_existing', lambda *a: pytest.fail('must reject first'))
    with pytest.raises(ValueError, match='仅支持'):
        gguf.install_gguf('https://example.com/model.gguf', 'ollama', tmp_path / 'new', Task(), lambda x: None)
    assert not (tmp_path / 'new').exists()


def test_download_uses_pinned_official_url_and_hash_before_rename(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    seen, logs, progress = [], [], []
    use_response(monkeypatch, Response(payload, len(payload)), seen)
    target = tmp_path / spec['filename']
    gguf._download(spec, target, Task(), logs.append, lambda p, t: progress.append(p))
    assert target.read_bytes() == payload
    assert not target.with_name(target.name + '.part').exists()
    assert '/resolve/' + gguf.REVISION + '/' in seen[0][0]
    assert seen[0][0].startswith('https://huggingface.co/ISTA-DASLab/')
    assert max(progress) <= 80
    assert 'PRIVATE_SIGNED_TOKEN' not in str(logs)


def test_wrong_hash_keeps_partial_and_does_not_publish_cache(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    use_response(monkeypatch, Response(b'x' * len(payload), len(payload)))
    target = tmp_path / spec['filename']
    with pytest.raises(RuntimeError, match='SHA-256'):
        gguf._download(spec, target, Task(), lambda x: None, None)
    assert not target.exists()
    assert target.with_name(target.name + '.part').read_bytes() == b'x' * len(payload)


def test_retry_restarts_partial_instead_of_appending(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']
    target.with_name(target.name + '.part').write_bytes(b'old partial which must not be appended')
    use_response(monkeypatch, Response(payload, len(payload)))
    gguf._download(spec, target, Task(), lambda x: None, None)
    assert target.read_bytes() == payload


def test_oversized_download_is_refused_without_writing_excess(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    use_response(monkeypatch, Response(payload + b'extra'))
    target = tmp_path / spec['filename']
    with pytest.raises(RuntimeError, match='超过'):
        gguf._download(spec, target, Task(), lambda x: None, None)
    assert not target.exists()
    assert target.with_name(target.name + '.part').stat().st_size <= len(payload)


def test_cancel_download_interrupts_socket_and_preserves_partial(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    task = Task()
    response = Response(payload, len(payload), task, cancel_read=1)
    use_response(monkeypatch, response)
    target = tmp_path / spec['filename']
    with pytest.raises(Cancelled):
        gguf._download(spec, target, task, lambda x: None, None)
    assert not target.exists() and target.with_name(target.name + '.part').exists()
    assert task.connection is None


def test_redirects_cannot_leave_https_official_hosts():
    handler = gguf._OfficialRedirect()
    for target in ['http://huggingface.co/x', 'https://evil.example/x', 'https://huggingface.co.evil.example/x', 'https://user:pass@huggingface.co/x']:
        with pytest.raises(RuntimeError, match='跳转'):
            handler.redirect_request(urllib.request.Request('https://huggingface.co/x'), None, 302, '', {}, target)
    assert gguf._official_url('https://cas-bridge.xethub.hf.co/x?signature=secret')
    assert gguf._official_url('https://cdn-lfs-us-1.hf.co/x')


def test_network_error_does_not_echo_signed_url(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    def fail(*a, **kw):
        raise urllib.error.URLError('https://us.aws.cdn.hf.co/a?signature=SECRET_VALUE')
    monkeypatch.setattr(gguf.urllib.request, 'build_opener', lambda *a: SimpleNamespace(open=fail))
    with pytest.raises(RuntimeError) as error:
        gguf._download(spec, tmp_path / spec['filename'], Task(), lambda x: None, None)
    assert 'SECRET_VALUE' not in str(error.value)


@pytest.mark.parametrize('source', ['other', 'unknown'])
def test_same_name_with_different_or_unknown_weights_is_not_overwritten(monkeypatch, small_spec, source):
    spec, payload = small_spec
    digest = '0' * 64 if source == 'other' else None
    modelfile = 'FROM C:/models/blobs/sha256-' + digest if digest else 'FROM C:/unverified/model.gguf'
    monkeypatch.setattr(gguf, '_request', lambda path, body, task: {'models': [{'name': TAG.casefold(), 'digest': 'sha256:manifest-not-weight'}]} if path == '/api/tags' else {'modelfile': modelfile})
    with pytest.raises(ValueError, match='未覆盖'):
        gguf._existing(spec, Task())


def test_existing_uses_weight_digest_not_manifest_digest(monkeypatch, small_spec):
    spec, payload = small_spec
    monkeypatch.setattr(gguf, '_request', lambda path, body, task: {'models': [{'name': TAG.casefold(), 'digest': 'sha256:NOT_THE_WEIGHT_HASH'}]} if path == '/api/tags' else {'modelfile': 'FROM "C:/models/blobs/sha256-' + spec['sha256'] + '"\nPARAMETER num_ctx 4096'})
    assert gguf._existing(spec, Task()) == TAG.casefold()


def test_valid_cache_is_rehashed_and_reused_before_cli_import(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']; target.write_bytes(payload)
    replies = iter([None, None, TAG.casefold()])
    imported = []
    monkeypatch.setattr(gguf, '_existing', lambda *a: next(replies))
    monkeypatch.setattr(gguf, '_download', lambda *a: pytest.fail('verified cache must be reused'))
    monkeypatch.setattr(gguf, '_import', lambda *a: imported.append(a[2]))
    assert gguf.install_gguf(TAG, 'ollama', tmp_path, Task(), lambda x: None) == TAG.casefold()
    assert imported == [target] and target.read_bytes() == payload


def test_unverified_existing_cache_is_not_overwritten(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']; target.write_bytes(b'personal existing file')
    monkeypatch.setattr(gguf, '_existing', lambda *a: None)
    monkeypatch.setattr(gguf, '_download', lambda *a: pytest.fail('existing file must not be overwritten'))
    with pytest.raises(ValueError, match='未覆盖'):
        gguf.install_gguf(TAG, 'ollama', tmp_path, Task(), lambda x: None)
    assert target.read_bytes() == b'personal existing file'


def test_recheck_refuses_model_created_by_another_client(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']; target.write_bytes(payload)
    calls = []
    def existing(*a):
        calls.append(1)
        if len(calls) == 1: return None
        raise ValueError('new conflicting model')
    monkeypatch.setattr(gguf, '_existing', existing)
    monkeypatch.setattr(gguf, '_import', lambda *a: pytest.fail('conflicting model must survive'))
    with pytest.raises(ValueError, match='conflicting'):
        gguf.install_gguf(TAG, 'ollama', tmp_path, Task(), lambda x: None)


def test_cli_modelfile_quotes_space_path_and_is_removed(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    cache = tmp_path / 'cache with spaces'; cache.mkdir()
    target = cache / spec['filename']; target.write_bytes(payload)
    captured, logs = {}, []
    class Process:
        returncode = 0
        stdout = io.StringIO('success\nhttps://signed.invalid?token=PRIVATE_TOKEN\n')
        def poll(self): return 0
    def popen(args, **kw):
        captured['args'] = args; captured['env'] = kw['env']
        captured['text'] = Path(args[-1]).read_text('utf-8')
        return Process()
    monkeypatch.setattr(gguf.subprocess, 'Popen', popen)
    task = Task()
    gguf._import(spec, 'C:/Program Files/Ollama/ollama.exe', target, cache, task, logs.append)
    assert captured['args'][1:3] == ['create', TAG]
    assert captured['text'] == 'FROM "' + target.resolve().as_posix() + '"\n'
    assert captured['env']['OLLAMA_HOST'] == '127.0.0.1:11434'
    assert not list(cache.glob('*.Modelfile'))
    assert 'PRIVATE_TOKEN' not in str(logs) and task.process is None


def test_cancel_after_cli_start_terminates_and_removes_temp_modelfile(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']; target.write_bytes(payload)
    task, stopped = Task(), []
    class Process:
        returncode = None
        stdout = io.StringIO('')
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 1; stopped.append(True)
        def wait(self, timeout): return self.returncode
    def popen(*a, **kw):
        task.event.set()
        return Process()
    monkeypatch.setattr(gguf.subprocess, 'Popen', popen)
    with pytest.raises(Cancelled):
        gguf._import(spec, 'ollama', target, tmp_path, task, lambda x: None)
    assert stopped and not list(tmp_path.glob('*.Modelfile')) and task.process is None
    assert target.read_bytes() == payload


def test_import_failure_retains_verified_cache(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']; target.write_bytes(payload)
    monkeypatch.setattr(gguf, '_existing', lambda *a: None)
    def fail(*a): raise RuntimeError('import failed')
    monkeypatch.setattr(gguf, '_import', fail)
    with pytest.raises(RuntimeError, match='import failed'):
        gguf.install_gguf(TAG, 'ollama', tmp_path, Task(), lambda x: None)
    assert target.read_bytes() == payload


def test_resume_range_verifies_the_complete_file(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    cut = 80
    target = tmp_path / spec['filename']
    target.with_name(target.name + '.part').write_bytes(payload[:cut])
    response = Response(payload[cut:], len(payload) - cut)
    response.status = 206
    response.headers['Content-Range'] = f'bytes {cut}-{len(payload)-1}/{len(payload)}'
    requests = []
    def open_request(request, timeout):
        requests.append(request.get_header('Range'))
        return response
    monkeypatch.setattr(gguf.urllib.request, 'build_opener', lambda *a: SimpleNamespace(open=open_request))
    gguf._download(spec, target, Task(), lambda x: None, None)
    assert requests == [f'bytes={cut}-'] and target.read_bytes() == payload


def test_resume_rejects_a_wrong_range_without_appending(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']
    partial = target.with_name(target.name + '.part')
    partial.write_bytes(payload[:80])
    response = Response(payload[80:], len(payload) - 80)
    response.status = 206
    response.headers['Content-Range'] = f'bytes 0-{len(payload)-1}/{len(payload)}'
    use_response(monkeypatch, response)
    with pytest.raises(RuntimeError, match='续传范围'):
        gguf._download(spec, target, Task(), lambda x: None, None)
    assert partial.read_bytes() == payload[:80] and not target.exists()


def test_corrupted_prefix_never_passes_full_hash(tmp_path, monkeypatch, small_spec):
    spec, payload = small_spec
    target = tmp_path / spec['filename']
    target.with_name(target.name + '.part').write_bytes(b'x' * 80)
    response = Response(payload[80:], len(payload) - 80)
    response.status = 206
    response.headers['Content-Range'] = f'bytes 80-{len(payload)-1}/{len(payload)}'
    use_response(monkeypatch, response)
    with pytest.raises(RuntimeError, match='SHA-256'):
        gguf._download(spec, target, Task(), lambda x: None, None)
    assert not target.exists()


@pytest.mark.parametrize('suffix', ['Q4_K_M', 'Q8_0', 'BF16'])
def test_precision_file_uses_its_own_pinned_repository(tmp_path, monkeypatch, suffix):
    tag = 'hf.co/ggml-org/Qwen3.8-27B-GGUF:Qwen3.8-27B-' + suffix + '.gguf'
    payload = b'GGUF synthetic exact content'
    spec = dict(gguf.FILES[tag.casefold()], size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    seen = []
    use_response(monkeypatch, Response(payload, len(payload)), seen)
    target = tmp_path / spec['filename']
    gguf._download(spec, target, Task(), lambda x: None, None)
    assert '/ggml-org/Qwen3.8-27B-GGUF/resolve/a316ea527fde32c12487be7574e2db5ac2ad2a7e/' in seen[0][0]
    assert 'ISTA-DASLab' not in seen[0][0]
    assert target.read_bytes() == payload
