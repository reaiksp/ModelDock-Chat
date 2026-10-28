import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatdock.engine import Engine
from chatdock.network import Client, Task
from chatdock.store import Store

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
models=Client().request("/api/tags",timeout=5).get("models",[])
models=[m["name"] for m in models if not m.get("remote_host") and not m.get("remote_model") and ":cloud" not in m["name"]]
if not models: raise SystemExit("No local model for compatible-API test")
config={"id":"local-api-smoke","name":"Ollama compatible API", "base":"http://127.0.0.1:11434/v1",
        "model":models[0],"context":4096,"key":"ollama","reasoning":"none","stream":True}
with tempfile.TemporaryDirectory(prefix="mdc-api-smoke-") as tmp:
    store=Store(Path(tmp)/"test.db")
    engine=Engine(store)
    engine.use_provider(config)
    session=store.new_session(route_id=config["id"])
    engine.chat(session,"请记住我的长期偏好：我一直喜欢简短的回答，不喜欢冗长解释。请确认。",Task(),lambda *a:None)
    engine.analyze_memory(session,Task(),lambda *a:None)
    memories=store.items("memories")
    assert memories and all(1<=m["score"]<=10 and m["summary"] and m["analysis"] for m in memories)
    learned=engine.learn("YYDS 的意思是永远的神，用于表达赞赏。例句：这波操作真是 YYDS。",Task(),lambda *a:None)
    terms=store.items("knowledge")
    assert learned["count"] and terms
    store.approve("knowledge",[m["id"] for m in terms])
    engine.chat(session,"YYDS 是什么意思？用一句话回答。",Task(),lambda *a:None)
    slang_reply=store.messages(session)[-1]["content"]
    store.set("response_language","en")
    engine.chat(session,"请简短打个招呼。",Task(),lambda *a:None)
    english_reply=store.messages(session)[-1]["content"]
    assert not any("\u4e00"<=c<="\u9fff" for c in english_reply), english_reply
    result={"status":"passed","english_reply_preference":True,"model":engine.model,"api_chat":True,"three_dimension_memory":True,
            "slang_api_learning":True,"retrieval_answer":slang_reply,"english_reply":english_reply}
    output=Path(__file__).resolve().parents[1]/"artifacts/api-upgrade-integration.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),"utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
