import io
import json
import math
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chatdock.hardware import Hardware, recommend
from chatdock.network import Task
from chatdock.speech import SpeechRecords, SpeechService, chunks, inspect_wav, readout_text
from chatdock.store import Store
from chatdock.tts_server import Runtime


def wav_bytes(silent=False):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"".join(struct.pack("<h", 0 if silent else int(8000 * math.sin(i / 12))) for i in range(2400)))
    return data.getvalue()


def test_joint_plans_reserve_resources_and_pair_voice_variants():
    hw = Hardware(32, 25, 16, 100, 100)
    off = recommend(hw)
    small = recommend(hw, True, "0.6B")
    large = recommend(hw, True, "1.7B")
    assert off["memory"] >= small["memory"] >= large["memory"]
    assert large["reserve_gpu"] == 7
    assert small["reserve_gpu"] == 4
    assert large["memory"] + large["reserve_gpu"] <= 16 - 16 * .12
    assert recommend(hw, True, "auto", preference="quality")["voice"] == "1.7B"


def test_cpu_and_low_resources_do_not_promise_concurrent_tts():
    assert recommend(Hardware(16, 10, 0, 100, 100))
    assert recommend(Hardware(16, 10, 0, 100, 100), True) is None
    assert recommend(Hardware(16, 6, 16, 100, 100), True, "1.7B") is None
    assert recommend(Hardware(32, 25, 16, 10, 100), True, "1.7B") is None
    assert recommend(Hardware(32, 25, 16, 100, 1), True, "1.7B") is None


def test_combined_disk_budget_applies_only_to_shared_drive():
    hw = Hardware(32, 25, 16, 100, 20)
    assert recommend(hw, True, "1.7B", same_disk=True) is None
    assert recommend(hw, True, "1.7B", same_disk=False)


def test_readout_does_not_rewrite_inputs_and_dictionary_is_single_pass():
    original = "# 标题\n**yyds** 和 [链接](https://example.org)\n```python\nprint('隐私')\n```\n永远的神。"
    result = readout_text(original, "yyds=永远的神\n永远的神=非常厉害")
    assert "print" not in result and "https:" not in result
    assert "永远的神" in result and "非常厉害" in result
    assert "yyds" in original and "print" in original
    assert "print" in readout_text(original, skip_code=False)
    with pytest.raises(ValueError):
        readout_text("x", "invalid")


def test_long_unicode_segments_preserve_text_and_bound_requests():
    text = "你好，世界。" * 300 + "\nEnglish mixed in!"
    parts = chunks(text)
    assert all(0 < len(part) <= 220 for part in parts)
    assert "".join(parts) == text.replace("\n", "")
    with pytest.raises(ValueError):
        chunks(" " * 30)
    with pytest.raises(ValueError):
        chunks("字" * 30001)


def test_wav_checks_report_measured_values_instead_of_subjective_scores():
    metrics = inspect_wav(wav_bytes())
    assert metrics["seconds"] == .1
    assert metrics["rms"] > .05
    assert not metrics["warnings"]
    assert "naturalness" not in metrics
    assert "near_silence" in inspect_wav(wav_bytes(True))["warnings"]
    with pytest.raises((ValueError, wave.Error, EOFError)):
        inspect_wav(b"invalid")
    with pytest.raises(ValueError):
        inspect_wav(wav_bytes()[:-50])


def test_speech_records_preserve_original_and_separate_human_ratings(tmp_path):
    store = Store(tmp_path / "chat.db")
    session = store.new_session()
    records = SpeechRecords(store)
    ident = records.begin(session, None, "原始 yyds", "原始 永远的神", {"speaker": "Vivian"})
    records.finish(ident, {"paths": [], "metrics": []})
    records.review(ident, 8, 7, 9, "清晰", "多音字需要修正")
    with pytest.raises(ValueError):
        records.review(ident, 11, 7, 9, "x", "")
    row = store.all("SELECT * FROM speech_runs")[0]
    assert row["original"] == "原始 yyds" and row["readout"] == "原始 永远的神"
    assert not store.items("memories")
    store.delete_session(session)
    assert not store.all("SELECT * FROM speech_runs")
    assert not store.all("SELECT * FROM speech_reviews")


def test_bridge_validates_input_before_loading_model():
    runtime = Runtime({"repo": "test", "device": "cuda:0"})
    for body in [{}, {"text": "x" * 501, "request_id": "a"},
                 {"text": "ok", "request_id": "a", "speaker": "invalid"}]:
        with pytest.raises(ValueError):
            runtime.synthesize(body)
    assert runtime.model is None


@pytest.fixture
def mock_speech(tmp_path):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            raw = wav_bytes() if self.path == "/speech" else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav" if self.path == "/speech" else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = SpeechService(tmp_path)
    service.ready.write_text(json.dumps({"host": "127.0.0.1", "port": server.server_port, "token": "x" * 32}), "utf-8")
    yield service, requests
    server.shutdown()
    server.server_close()


def test_http_synthesis_sends_only_readout_not_chat_memory_or_persona(mock_speech, tmp_path):
    service, requests = mock_speech
    events = []
    result = service.generate("你好。世界！", {"speaker": "Vivian", "language": "Chinese", "instruct": "温和"},
                              tmp_path / "audio", Task(), lambda *args:events.append(args))
    assert len(result["paths"]) == 2 and len(events) == 2
    assert all(path == "/speech" and auth == "Bearer " + "x" * 32 for path, body, auth in requests)
    assert set(requests[0][1]) == {"text", "speaker", "language", "instruct", "request_id"}


def test_cancel_between_segments_does_not_send_more_text(mock_speech, tmp_path):
    service, requests = mock_speech
    task = Task()
    from chatdock.network import Cancelled
    with pytest.raises(Cancelled):
        service.generate("你好。世界！", {"speaker": "Vivian", "language": "Chinese"},
            tmp_path / "audio", task, lambda *args: task.cancel())
    assert len(requests) == 1


def test_early_cancel_prevents_model_loading():
    runtime = Runtime({"repo": "test", "device": "cuda:0"})
    runtime.cancel("early")
    with pytest.raises(InterruptedError):
        runtime.synthesize({"request_id": "early", "text": "你好"})
    assert runtime.model is None
    assert not runtime.events
    assert runtime.lock.acquire(blocking=False)
    runtime.lock.release()


def test_real_loopback_bridge_auth_health_and_shutdown(tmp_path):
    import http.client
    from chatdock.tts_server import serve
    token = "x" * 40
    ready = tmp_path / "ready.json"
    thread = threading.Thread(target=serve, args=({"repo":"test", "device":"cpu", "token":token}, ready), daemon=True)
    thread.start()
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(.01)
    endpoint = json.loads(ready.read_text())
    assert endpoint["host"] == "127.0.0.1"
    def request(path, auth="", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=3)
        raw = json.dumps(body).encode() if body is not None else None
        connection.request("POST" if body is not None else "GET", path, raw,
            {"Authorization":auth, "Content-Type":"application/json"})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result
    try:
        assert request("/health")[0] == 401
        code, data = request("/health", "Bearer " + token)
        assert code == 200 and data["protocol"] == "modeldock-tts-v1" and not data["loaded"]
        assert request("/speech", "Bearer " + token, {"request_id":"bad", "text":"字" * 501})[0] == 400
    finally:
        request("/shutdown", "Bearer " + token, {})
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_api_text_mode_reserves_only_local_speech_resources():
    hw = Hardware(16, 6.5, 10, 100, 0)
    assert recommend(hw, True, "1.7B") is None
    plan = recommend(hw, True, "1.7B", remote_text=True)
    assert plan["text"] == "API" and plan["memory"] == 0 and plan["voice"] == "1.7B"


def test_app_smoke_releases_audio_before_windows_temp_cleanup(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "startup.json"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run([sys.executable, str(root / "main.py"), "--smoke-test", str(report)],
        cwd=root, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    data = json.loads(report.read_text())
    assert data["startup"] == "passed" and data["pages"] == 6
    assert data["audio_backend_loaded"] and data["speech_bridge_bundled"]
