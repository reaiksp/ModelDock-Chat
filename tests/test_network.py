import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chatdock.network import Cancelled, Client, Task, model_context, validate_url
from chatdock.secrets import Vault
from chatdock.engine import Engine
from chatdock.store import Store


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.received.append((self.path, body))
            if body.get("model") == "bad":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'SECRET_API_KEY')
                return
            self.send_response(200)
            self.end_headers()
            if self.path == "/api/show":
                self.wfile.write(b'{"model_info":{"qwen3.context_length":32768}}')
            elif self.path.endswith("/chat/completions"):
                content = json.dumps({"entries": [{"term": "YYDS", "definition": "Test meaning", "aliases": "yyds", "example": "example"}]}) if body.get("model") == "learner" else "OK"
                self.wfile.write(json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}).encode())
            else:
                self.wfile.write(b'{"message":{"content":"hello"},"done":false}\n')
                if body.get("model") == "slow":
                    self.wfile.flush()
                    threading.Event().wait(2)
                    return
                if body.get("model") != "truncated":
                    self.wfile.write(b'{"message":{"content":" world"},"done":true}\n')
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.received = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_native_stream_and_model_context(server):
    client = Client(f"http://127.0.0.1:{server.server_port}")
    values = []
    assert client.chat("ok", [], Task(), values.append) == "hello world"
    assert values == ["hello", " world"]
    assert model_context(client, "ok", Task()) == 32768


def test_truncated_stream_is_error(server):
    with pytest.raises(RuntimeError, match="提前结束"):
        Client(f"http://127.0.0.1:{server.server_port}").chat("truncated", [], Task(), lambda _: None)


def test_cancel_interrupts_blocked_http10_stream(server):
    client = Client(f"http://127.0.0.1:{server.server_port}")
    task = Task()
    timers = []
    def token(_):
        timer = threading.Timer(.05, task.cancel)
        timers.append(timer)
        timer.start()
    with pytest.raises(Cancelled):
        client.chat("slow", [], task, token)
    for timer in timers:
        timer.join()


def test_external_provider_path_and_body(server):
    client = Client(f"http://127.0.0.1:{server.server_port}/v1", "secret")
    assert client.completion("api-model", [{"role": "user", "content": "材料"}], Task()) == "OK"
    assert server.received[-1][0] == "/v1/chat/completions"
    assert len(server.received[-1][1]["messages"]) == 1


def test_error_does_not_echo_server_secrets(server):
    with pytest.raises(RuntimeError) as error:
        Client(f"http://127.0.0.1:{server.server_port}").request("/api/show", {"model": "bad"})
    assert "401" in str(error.value)
    assert "SECRET_API_KEY" not in str(error.value)


def test_external_learning_never_sends_main_chat_or_memories(server, tmp_path):
    store = Store(tmp_path / "test.db")
    session = store.new_session()
    store.add_message(session, "user", "PRIVATE_CHAT")
    store.add_memory("PRIVATE_MEMORY", status="approved")
    store.set("user_profile", "PRIVATE_PROFILE")
    engine = Engine(store)
    config = {"base": f"http://127.0.0.1:{server.server_port}/v1", "key": "test-key", "model": "learner", "context": 4096}
    result = engine.learn("YYDS means test", Task(), lambda *args: None, config)
    assert result["count"] == 1
    assert "PRIVATE" not in str(server.received)
    assert "YYDS means test" in str(server.received)
    assert store.items("knowledge")[0]["status"] == "pending"


@pytest.mark.parametrize("url", ["http://remote.example/v1", "file:///etc/passwd", "https://user:password@example.com/v1", "https://example.com/v1?key=secret"])
def test_unsafe_provider_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_vault_encrypts_round_trip_and_can_clear(tmp_path):
    vault = Vault(tmp_path / "key.dpapi")
    vault.save("secret-测试-123")
    assert "secret".encode() not in vault.path.read_bytes()
    assert vault.load() == "secret-测试-123"
    vault.save("")
    assert vault.load() == ""
