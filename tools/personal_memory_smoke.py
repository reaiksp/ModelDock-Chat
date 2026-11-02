"""Real local personal-memory smoke test, using only synthetic temporary data.

Uses one already-installed qwen3.5-9b model, creates temporary private SYSTEM
snapshots, verifies ordinary Ollama default-prompt behavior and automatic
continuation, then removes every snapshot created by this invocation. No user
chat database, speech runtime, remote API, model download or training is used.
"""
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatdock.engine import Engine
from chatdock.network import Client, Task, model_context
from chatdock.personal_model import delete_snapshot, verify_snapshot
from chatdock.store import Store

BASE = "qwen3.5-9b:latest"
CONTEXT = 4096
CODE = "琥珀灯塔731"
PENDING = "霜原830"
OUTPUT = Path(__file__).resolve().parents[1] / "artifacts/personal-memory-integration.json"


class TrackingClient(Client):
    def __init__(self):
        super().__init__()
        self.created_names = []

    def request(self, path, body=None, task=None, timeout=180):
        if path == "/api/create" and body and body.get("model", "").startswith("mdc-private-"):
            self.created_names.append(body["model"])
        return super().request(path, body, task, timeout)


def canonical(name):
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def run():
    started = time.monotonic()
    client = TrackingClient()
    report = {"status": "running", "model": BASE, "context": CONTEXT,
              "data": "synthetic temporary database only", "weight_training": False,
              "model_download": False, "speech_started": False, "cleanup": []}
    initial = {}
    try:
        if len(sys.argv) > 1 and sys.argv[1] != BASE:
            raise ValueError("This smoke test only permits the already-installed " + BASE)
        report["ollama_version"] = client.request("/api/version", timeout=10).get("version")
        rows = client.request("/api/tags", timeout=10).get("models", [])
        initial = {canonical(row["name"]): row.get("digest") for row in rows}
        if BASE not in initial:
            raise RuntimeError("Required local model is absent; this test will not download it.")
        with tempfile.TemporaryDirectory(prefix="mdc-personal-memory-smoke-") as tmp:
            store = Store(Path(tmp) / "synthetic.db")
            store.save_persona(1, "测试人格", "这是合成测试。只用一行短答，回答以“测试：”开头；不要解释测试过程。")
            store.set("user_profile", "这是虚构的测试用户；偏好简短中文回答。")
            approved = store.add_memory("测试用暗号是" + CODE + "。", status="approved", score=10,
                analysis="这是合成测试明确提供的暗号，仅用于验证默认提示的读取。")
            pending = store.add_memory("未确认暗号是" + PENDING + "。", status="pending", score=10)
            session = store.new_session(1, "合成记忆实验")
            engine = Engine(store, client)
            engine.model = BASE
            engine.local_model = BASE
            engine.context = CONTEXT
            engine.model_limit = model_context(client, BASE, Task())
            if engine.budget.context != CONTEXT:
                raise RuntimeError("The installed model cannot confirm the required 4096 context.")
            events = []
            emit = lambda kind, value: events.append((kind, value))
            first = engine.sync_personal_model(session, Task(), emit, force=True)
            first_snapshot = engine.personal_snapshot(session)
            first_info = client.request("/api/show", {"model": first["name"]}, timeout=30)
            assert first_info.get("system") == first_snapshot["system"], "Saved SYSTEM differs"
            assert verify_snapshot(client, first["name"], first_snapshot, Task(), CONTEXT), "Snapshot verification failed"
            assert CODE in first_info["system"] and PENDING not in first_info["system"], "Approval isolation failed"
            assert first["manifest"]["included_ids"] == [approved] and pending not in first["manifest"]["included_ids"]
            report["system_and_context_verified"] = True
            report["approved_candidate_isolation"] = True
            report["initial_snapshot_fingerprint"] = first["fingerprint"]
            print("Initial personal snapshot verified; testing the model's default SYSTEM.", flush=True)

            prompts = ["请告诉我已记住的测试暗号。", "请遵循保存的默认人格，以“测试：”开头，只写已记住的测试暗号。"]
            answers = []
            behavior_ok = False
            for prompt in prompts:
                answer = client.chat(first["name"], [{"role": "user", "content": prompt}], Task(),
                    lambda _: None, context=CONTEXT, reply=128).strip()
                answers.append(answer)
                behavior_ok = CODE in re.sub(r"\s+", "", answer) and answer.startswith("测试：") and len(answer) <= 100
                if behavior_ok:
                    break
            report["recall_comparison"] = "ignore whitespace only; preserve model output verbatim"
            report["default_system_attempts"] = len(answers)
            report["default_system_synthetic_answers"] = answers
            report["default_system_recall_and_short_persona"] = behavior_ok
            assert behavior_ok, "Model failed synthetic default-SYSTEM recall/personality check"
            print("Default SYSTEM recall and short-persona behavior passed; testing continuation.", flush=True)

            # Stop adding pairs as soon as the real conservative budget triggers
            # a rollover. The summarizer itself must budget/chunk this material.
            raw_input = "继续测试，只用一句话说明下一步。"
            for index in range(16):
                store.add_message(session, "user", f"第{index+1}轮合成任务：验证本机个人记忆配置，保留测试原文与简洁偏好。" * 2)
                store.add_message(session, "assistant", f"第{index+1}轮计划：先核对配置，再验证自动概括和后续对话；所有材料均为虚构。" * 2)
                if engine.preview(session, raw_input)["rollover"]:
                    break
            assert engine.preview(session, raw_input)["rollover"], "Synthetic history did not trigger rollover"
            before = store.messages(session)
            store.set("personal_model_auto", True)
            target = engine.chat(session, raw_input, Task(), emit)
            checkpoints = store.checkpoints(session)
            assert target != session and store.session(target)["parent_id"] == session, "No linked continuation"
            assert store.messages(session) == before, "Original synthetic history was changed"
            assert checkpoints and checkpoints[0]["target_session"] == target and checkpoints[0]["summary"].strip(), "No persisted checkpoint"
            outgoing = store.messages(target)
            assert outgoing[0]["content"] == raw_input and outgoing[0]["status"] == "complete", "Raw input was not preserved"
            assert outgoing[-1]["role"] == "assistant" and outgoing[-1]["content"].strip(), "Continuation has no final reply"
            versions = store.personal_models()
            latest = versions[0]
            assert len(versions) >= 2 and latest["name"] != first["name"], "Automatic refresh did not create a new version"
            assert latest["manifest"].get("verified", True) and latest["manifest"]["summary_included"], "Summary snapshot is not verified"
            latest_snapshot = engine.personal_snapshot(target)
            assert verify_snapshot(client, latest["name"], latest_snapshot, Task(), CONTEXT), "Continuation SYSTEM/context verification failed"
            assert PENDING not in latest_snapshot["system"], "Candidate leaked into automatic snapshot"
            report.update({"automatic_rollover": True, "checkpoint_persisted": True,
                           "automatic_new_snapshot": True, "original_history_unchanged": True,
                           "raw_user_input_preserved": True, "original_history_messages": len(before),
                           "checkpoint_summary_bytes": len(checkpoints[0]["summary"].encode("utf-8")),
                           "checkpoint_summary_sha256": hashlib.sha256(checkpoints[0]["summary"].encode("utf-8")).hexdigest(),
                           "snapshot_versions": len(versions), "continuation_reply": outgoing[-1]["content"],
                           "new_snapshot_fingerprint": latest["fingerprint"]})
            print("Automatic summary, checkpoint, new snapshot and original-text checks passed.", flush=True)
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Never infer ownership from a prefix alone: only remove names this
        # TrackingClient created and that were absent before this invocation.
        for name in reversed(list(dict.fromkeys(client.created_names))):
            if canonical(name) in initial:
                report["cleanup"].append({"name": name, "removed": False, "error": "Pre-existing name; never delete"})
                report["status"] = "failed"
                continue
            try:
                delete_snapshot(client, name, Task())
                report["cleanup"].append({"name": name, "removed": True})
            except Exception as exc:
                report["cleanup"].append({"name": name, "removed": False, "error": f"{type(exc).__name__}: {exc}"})
                report["status"] = "failed"
        try:
            remaining = {canonical(r["name"]): r.get("digest") for r in client.request("/api/tags", timeout=15).get("models", [])}
            report["base_model_preserved"] = BASE in initial and remaining.get(BASE) == initial[BASE]
            report["all_test_snapshots_removed"] = all(canonical(n) not in remaining for n in client.created_names)
            if not report["base_model_preserved"] or not report["all_test_snapshots_removed"]:
                report["status"] = "failed"
        except Exception as exc:
            report["status"] = "failed"
            report["final_inventory_error"] = f"{type(exc).__name__}: {exc}"
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(OUTPUT),
                          "elapsed_seconds": report["elapsed_seconds"],
                          "created_snapshot_count": len(client.created_names),
                          "all_test_snapshots_removed": report.get("all_test_snapshots_removed"),
                          "error": report.get("error")}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run())
