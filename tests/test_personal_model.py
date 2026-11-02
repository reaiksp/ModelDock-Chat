"""The experiment persists SYSTEM data, never modifies weights or source records."""
import copy

import pytest

from chatdock.context import Budget, message_cost
from chatdock.network import Cancelled, Task
from chatdock.personal_model import build_snapshot, sync_snapshot
from chatdock.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "personal.db")


class LocalClient:
    base = "http://127.0.0.1:11434"

    def __init__(self):
        self.calls = []
        self.models = [{"name": "qwen3:4b"}]
        self.base_info = {"model_info": {"qwen3.context_length": 8192}}
        self.created = None
        self.create_result = {"status": "success"}
        self.cancel_on_create = False
        self.fail_verify_once = False
        self.verify_override = None

    def request(self, path, body=None, task=None, timeout=180):
        if task:
            task.check()
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/api/tags":
            return {"models": copy.deepcopy(self.models)}
        if path == "/api/create":
            self.created = copy.deepcopy(body)
            if self.cancel_on_create:
                task.cancel()
            return self.create_result
        if path == "/api/show" and body["model"] == "qwen3:4b":
            return copy.deepcopy(self.base_info)
        if path == "/api/show" and self.created:
            if self.fail_verify_once:
                self.fail_verify_once = False
                raise RuntimeError("verification connection failure")
            info = {"system": self.created["system"],
                    "parameters": "num_ctx " + str(self.created["parameters"]["num_ctx"])}
            return self.verify_override if self.verify_override is not None else info
        raise RuntimeError("not found")


def test_snapshot_approved_only_and_preserves_sources(store):
    store.set("user_profile", "我的偏好原文，保持不变。")
    good = store.add_memory("喜欢简洁", score=8, analysis="明确表达", status="approved")
    store.add_memory("PENDING_SECRET", status="pending")
    original = store.items("memories")
    result = build_snapshot(store, 1, 4096, summary="用户正在完成一个任务。")
    assert result["included_ids"] == [good]
    assert "喜欢简洁" in result["system"]
    assert "PENDING_SECRET" not in result["system"]
    assert "我的偏好原文，保持不变。" in result["system"]
    assert "不视为用户确认事实" in result["system"]
    assert store.items("memories") == original
    assert store.get("user_profile") == "我的偏好原文，保持不变。"
    assert result["summary_included"] and result["profile_included"]
    assert result["used"] <= Budget(4096).input_limit


def test_optional_persona_and_reply_language(store):
    store.set("response_language", "en")
    original = store.persona(1)
    included = build_snapshot(store, 1, 4096)
    excluded = build_snapshot(store, 1, 4096, include_persona=False)
    assert original["prompt"] in included["system"]
    assert original["prompt"] not in excluded["system"]
    assert "ENGLISH" in excluded["system"]
    assert included["fingerprint"] != excluded["fingerprint"]
    assert store.persona(1) == original


def test_whole_memory_items_omitted_in_priority_order(store):
    low = store.add_memory("低优先" * 60, score=1, analysis="低分析" * 40, status="approved")
    high = store.add_memory("高优先" * 60, score=10, analysis="高分析" * 40, status="approved")
    medium = store.add_memory("中优先" * 60, score=5, analysis="中分析" * 40, status="approved")
    snapshot = build_snapshot(store, 1, 4096)
    assert snapshot["included_ids"][0] == high
    assert snapshot["omitted_count"] >= 1
    assert set(snapshot["included_ids"] + snapshot["omitted_ids"]) == {low, high, medium}
    for row in store.items("memories", "approved"):
        if row["id"] in snapshot["included_ids"]:
            assert row["summary"] in snapshot["system"]
            assert row["analysis"] in snapshot["system"]
        else:
            assert row["summary"] not in snapshot["system"]
    assert message_cost([{"role": "system", "content": snapshot["system"]},
                         {"role": "user", "content": "x" * 512}]) == snapshot["used"]


def test_oversized_fixed_fields_fail_instead_of_truncating(store):
    store.set("user_profile", "完整资料" * 1000)
    with pytest.raises(ValueError, match="预算"):
        build_snapshot(store, 1, 4096)
    assert store.get("user_profile") == "完整资料" * 1000
    store.set("user_profile", "")
    with pytest.raises(ValueError, match="预算"):
        build_snapshot(store, 1, 4096, summary="完整摘要" * 1000)
    with pytest.raises(ValueError, match="预算"):
        build_snapshot(store, 1, 1024)


@pytest.mark.parametrize("context", [True, 0, -1, 4096.0, "4096"])
def test_invalid_context_rejected(store, context):
    with pytest.raises(ValueError):
        build_snapshot(store, 1, context)


def test_fingerprint_is_stable_but_changes_with_content(store):
    first = build_snapshot(store, 1, 4096)
    assert build_snapshot(store, 1, 4096) == first
    store.add_memory("新偏好", status="approved")
    assert build_snapshot(store, 1, 4096)["fingerprint"] != first["fingerprint"]


def test_sync_creates_new_model_and_verifies_system_and_context(store):
    snapshot = build_snapshot(store, 1, 4096)
    client = LocalClient()
    name = sync_snapshot(client, "qwen3:4b", "mdc-private-test-r1", snapshot, Task(), 4096)
    assert name == "mdc-private-test-r1"
    assert client.created["from"] == "qwen3:4b"
    assert client.created["system"] == snapshot["system"]
    assert client.created["parameters"] == {"num_ctx": 4096}
    assert [c[0] for c in client.calls] == ["/api/tags", "/api/show", "/api/create", "/api/show"]


@pytest.mark.parametrize("name", ["qwen3", "BadName", "../bad", "name:latest", "x" * 62])
def test_unsafe_or_existing_name_never_created(store, name):
    client = LocalClient()
    client.models.append({"name": "qwen3:latest"})
    with pytest.raises(ValueError):
        sync_snapshot(client, "qwen3:4b", name, build_snapshot(store, 1, 4096), Task(), 4096)
    assert client.created is None


def test_base_name_cannot_be_overwritten(store):
    with pytest.raises(ValueError, match="不能覆盖"):
        sync_snapshot(LocalClient(), "qwen3", "qwen3", build_snapshot(store, 1, 4096), Task(), 4096)


@pytest.mark.parametrize("remote", ["source", "show_host", "show_model", "capabilities", "name"])
def test_cloud_sources_cannot_be_snapshotted(store, remote):
    client = LocalClient()
    base = "qwen3:4b"
    if remote == "source":
        client.models[0]["remote_host"] = "https://remote.invalid"
    elif remote == "show_host":
        client.base_info["remote_host"] = "https://remote.invalid"
    elif remote == "show_model":
        client.base_info["remote_model"] = "remote"
    elif remote == "capabilities":
        client.base_info["capabilities"] = ["completion", "cloud"]
    else:
        base = "qwen3:cloud"
        client.models[0]["name"] = base
        old_request = client.request
        client.request = lambda path, body=None, task=None, **kw: copy.deepcopy(client.base_info) if path == "/api/show" and body["model"] == base else old_request(path, body, task, **kw)
    with pytest.raises(ValueError, match="云端模型"):
        sync_snapshot(client, base, "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert client.created is None


def test_external_endpoint_cannot_receive_private_snapshot(store):
    client = LocalClient()
    client.base = "https://provider.invalid/v1"
    with pytest.raises(ValueError, match="本机 Ollama"):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert not client.calls


def test_context_above_actual_or_unknown_limit_is_rejected(store):
    client = LocalClient()
    snapshot = build_snapshot(store, 1, 16384)
    with pytest.raises(ValueError, match="元数据"):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", snapshot, Task(), 16384)
    assert client.created is None
    client.base_info = {}
    with pytest.raises(ValueError, match="元数据"):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 8192), Task(), 8192)
    assert client.created is None


def test_pre_cancelled_task_never_sends_data(store):
    client, task = LocalClient(), Task()
    task.cancel()
    with pytest.raises(Cancelled):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), task, 4096)
    assert not client.calls


def test_cancellation_after_create_uses_independent_task_to_cleanup(store, monkeypatch):
    client = LocalClient()
    client.cancel_on_create = True
    deleted = []
    monkeypatch.setattr("chatdock.personal_model._delete_local", lambda c, n: deleted.append(n))
    with pytest.raises(Cancelled):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert deleted == ["mdc-private-new"]


def test_verification_connection_failure_cleans_fresh_model(store, monkeypatch):
    client = LocalClient()
    client.fail_verify_once = True
    deleted = []
    monkeypatch.setattr("chatdock.personal_model._delete_local", lambda c, n: deleted.append(n))
    with pytest.raises(RuntimeError, match="verification connection"):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert deleted == ["mdc-private-new"]


def test_failed_create_result_still_cleans_only_matching_fresh_model(store, monkeypatch):
    client = LocalClient()
    client.create_result = {"status": "error"}
    deleted = []
    monkeypatch.setattr("chatdock.personal_model._delete_local", lambda c, n: deleted.append(n))
    with pytest.raises(RuntimeError, match="未确认"):
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert deleted == ["mdc-private-new"]


def test_mismatched_created_model_is_not_deleted(store, monkeypatch):
    client = LocalClient()
    client.verify_override = {"system": "created elsewhere", "parameters": "num_ctx 4096"}
    deleted = []
    monkeypatch.setattr("chatdock.personal_model._delete_local", lambda c, n: deleted.append(n))
    with pytest.raises(RuntimeError, match="校验失败") as error:
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert not deleted
    assert error.value.personal_model_cleanup_pending == "mdc-private-new"


def test_cleanup_failure_preserves_original_exception_and_names_orphan(store, monkeypatch):
    client = LocalClient()
    client.cancel_on_create = True
    def fail_delete(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr("chatdock.personal_model._delete_local", fail_delete)
    with pytest.raises(Cancelled) as error:
        sync_snapshot(client, "qwen3:4b", "mdc-private-new", build_snapshot(store, 1, 4096), Task(), 4096)
    assert error.value.personal_model_cleanup_pending == "mdc-private-new"



def test_delete_only_accepts_private_revision_names(monkeypatch):
    from chatdock.personal_model import delete_snapshot
    deleted = []
    monkeypatch.setattr("chatdock.personal_model._delete_local", lambda c, n, t: deleted.append(n))
    with pytest.raises(ValueError, match="只能删除"):
        delete_snapshot(LocalClient(), "qwen3", Task())
    assert not deleted
    assert delete_snapshot(LocalClient(), "mdc-private-a-r1", Task()) == "mdc-private-a-r1"
    assert deleted == ["mdc-private-a-r1"]


def test_delete_uses_delete_method_and_clears_task_resources(monkeypatch):
    import json
    from chatdock.personal_model import delete_snapshot
    calls = []
    class Connection:
        sock = object()
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
        def connect(self):
            pass
        def request(self, method, path, body, headers):
            calls.append((method, path, json.loads(body)))
        def getresponse(self):
            return self
        status = 200
        def read(self, maximum):
            return b""
        def close(self):
            calls.append("closed")
    monkeypatch.setattr("chatdock.personal_model.http.client.HTTPConnection", Connection)
    task = Task()
    delete_snapshot(LocalClient(), "mdc-private-a-r1", task)
    assert calls == [("127.0.0.1", 11434, 10),
                     ("DELETE", "/api/delete", {"model": "mdc-private-a-r1"}), "closed"]
    assert task.connection is None and task.socket is None
