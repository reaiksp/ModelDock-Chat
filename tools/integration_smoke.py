"""Local-only real integration. Creates no user history or persistent model variants."""
import json
import sys
import tempfile
import http.client
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatdock.engine import Engine
from chatdock.network import Client, Task, model_context
from chatdock.store import Store

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
client = Client()
models = client.request("/api/tags", timeout=5).get("models", [])
models = [m["name"] for m in models if not m.get("remote_model") and not m.get("remote_host") and ":cloud" not in m.get("name", "")]
if not models:
    raise SystemExit("No installed local model available")
model = sys.argv[1] if len(sys.argv) > 1 else models[0]
print("Real local integration with", model, flush=True)
with tempfile.TemporaryDirectory(prefix="modeldock-chat-integration-") as tmp:
    store = Store(Path(tmp) / "chat.db")
    engine = Engine(store, client)
    engine.model = model
    engine.model_limit = model_context(client, model, Task())
    session = store.new_session()
    learned = engine.learn("项目测试用黑话：绿灯熊的定义是“已经通过测试的功能”。例句：这个功能现在是绿灯熊。", Task(), lambda k, v: print(v, flush=True))
    knowledge = store.items("knowledge")
    assert knowledge and learned["count"], "No learned slang"
    store.approve("knowledge", [k["id"] for k in knowledge])
    session = engine.chat(session, "用一句话说明绿灯熊是什么意思。", Task(), lambda *args: None)
    answer = store.messages(session)[-1]["content"]
    assert answer.strip()
    engine.chat(session, "请记住：我喜欢简洁的回答。", Task(), lambda *args: None)
    engine.analyze_memory(session, Task(), lambda *args: None)
    candidates = store.items("memories")
    assert candidates, "No user memory candidates"
    variant = "modeldock-chat-smoke-" + uuid.uuid4().hex[:10]
    variant_ok = False
    try:
        engine.create_variant(1, variant, Task())
        info = client.request("/api/show", {"model": variant})
        assert "绿灯熊" in info.get("system", "")
        variant_ok = True
    finally:
        conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=30)
        try:
            conn.request("DELETE", "/api/delete", json.dumps({"model": variant}), {"Content-Type": "application/json"})
            response = conn.getresponse()
            response.read()
            assert response.status in {200, 404}, "Could not clean up temporary model variant"
        finally:
            conn.close()
    # Validate length rejection against the same real engine without making a request.
    assert engine.preview(session, "中" * 5000)["too_long"]
    report = {"status": "passed", "model": model, "model_context_limit": engine.model_limit,
        "slang_learning": learned, "retrieval_chat": answer,
        "memory_candidates": len(candidates), "oversized_prompt_blocked": True,
        "ollama_variant_created_and_removed": variant_ok}
    output = Path(__file__).resolve().parents[1] / "artifacts/integration-result.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
