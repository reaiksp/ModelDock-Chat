"""Experimental persistent personal SYSTEM snapshots; never weight training.

The caller keeps the manifest and chooses a fresh private model name for every
revision. MDC's own chat still sends its live system prompt, which overrides an
Ollama model's default SYSTEM; these snapshots also work from other clients.
"""
import hashlib
import http.client
import json
import re
from urllib.parse import urlsplit

from .context import Budget, message_cost, system_prompt
from .network import Task

RESERVED_INPUT = 512
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,60}\Z")


def _messages(system):
    return [{"role": "system", "content": system},
            {"role": "user", "content": " " * RESERVED_INPUT}]


def build_snapshot(store, persona_id, context, summary="", include_persona=True):
    """Build a bounded, inspectable snapshot without changing any source record.

    Persona, profile, summary and individual memory items are never silently
    truncated. Fixed fields that do not fit cause an error; whole memory items
    that do not fit are omitted and reported by ID/count.
    """
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        raise ValueError("模型上下文必须是正整数。")
    if not isinstance(summary, str) or not isinstance(include_persona, bool):
        raise ValueError("个人模型摘要或人格选项无效。")
    persona = store.persona(persona_id)["prompt"] if include_persona else ""
    profile = store.get("user_profile", "")
    if not isinstance(profile, str):
        raise ValueError("用户资料格式无效，请重新保存。")
    language = store.get("response_language", "auto")
    memories = ["用户自行填写的资料：" + profile] if profile else []
    budget = Budget(context)

    def render(rows):
        return system_prompt(persona, rows, summary=summary, response_language=language)

    system = render(memories)
    if not budget.check(_messages(system))[0]:
        raise ValueError("人格、资料或摘要超出个人模型预算；请缩短内容或增加模型支持的上下文。")
    approved = sorted(store.items("memories", "approved"),
                      key=lambda row: (row["score"], row["id"]), reverse=True)
    included, omitted = [], []
    for row in approved:
        entry = {"score": row["score"], "summary": row["summary"],
                 "analysis": row["analysis"]}
        candidate = render(memories + [entry])
        if budget.check(_messages(candidate))[0]:
            memories.append(entry)
            included.append(row["id"])
            system = candidate
        else:
            omitted.append(row["id"])
    material = json.dumps({"schema": 1, "system": system, "context": context},
                          ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {"system": system, "fingerprint": hashlib.sha256(material).hexdigest(),
            "context": context, "included_ids": included, "omitted_ids": omitted,
            "omitted_count": len(omitted), "summary_included": bool(summary),
            "include_persona": include_persona, "profile_included": bool(profile),
            "used": message_cost(_messages(system)),
            "system_used": message_cost(_messages(system)[:1]),
            "input_limit": budget.input_limit, "reserved_input": RESERVED_INPUT}


def _canonical(name):
    name = name.casefold()
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def _local(client):
    parsed = urlsplit(getattr(client, "base", ""))
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port != 11434 or parsed.path not in {"", "/"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("个人记忆模型仅支持本机 Ollama，不能写入外部 API。")
    return parsed


def _remote(info, name=""):
    capabilities = info.get("capabilities", [])
    return (bool(info.get("remote_host") or info.get("remote_model"))
            or ":cloud" in name.casefold()
            or (isinstance(capabilities, (list, tuple, dict)) and "cloud" in capabilities))


def _context_value(info):
    parameters = info.get("parameters", "")
    if isinstance(parameters, dict):
        value = parameters.get("num_ctx")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(parameters, str):
        match = re.search(r"(?m)^\s*num_ctx\s+(\d+)\s*$", parameters)
        return int(match.group(1)) if match else None
    return None


def _delete_local(client, name, task=None):
    """Ollama DELETE is not available through the existing POST-only request API."""
    parsed = _local(client)
    task = task or Task()
    task.check()
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    task.connection = conn
    try:
        conn.connect()
        task.socket = conn.sock
        task.check()
        conn.request("DELETE", "/api/delete", json.dumps({"model": name}).encode("utf-8"),
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        response.read(1_000_001)
        task.check()
        if response.status not in {200, 404}:
            raise RuntimeError("个人模型清理失败，请在 Ollama 中检查。")
    except Exception:
        task.check()
        raise
    finally:
        conn.close()
        task.connection = None
        task.socket = None


def delete_snapshot(client, name, task):
    """Delete an owned private revision (the caller must verify its manifest)."""
    _local(client)
    if not isinstance(name, str) or not _NAME.fullmatch(name) or not name.startswith("mdc-private-"):
        raise ValueError("只能删除已记录的 MDC 个人模型版本。")
    task.check()
    _delete_local(client, name, task)
    return name


def _cleanup_created(client, name, system, context):
    # Independent task: the original task may already have been cancelled. A
    # same-name model with different content is never removed by this cleanup.
    info = client.request("/api/show", {"model": name}, Task(), timeout=10)
    if info.get("system") != system or _context_value(info) != context:
        raise RuntimeError("新模型归属无法确认，未自动删除。")
    _delete_local(client, name)


def verify_snapshot(client, name, snapshot, task, context):
    """Read back a managed version before reusing it; other clients may change it."""
    _local(client)
    task.check()
    info = client.request("/api/show", {"model": name}, task)
    return (info.get("system") == snapshot["system"]
            and _context_value(info) == context and not _remote(info, name))


def sync_snapshot(client, base, name, snapshot, task, context):
    """Create and verify a fresh local SYSTEM snapshot, never overwrite a model.

    Every revision needs a unique name. Failure/cancellation after CREATE starts
    triggers a best-effort cleanup after verifying its SYSTEM and context. The
    existing base and all models found before CREATE are never cleanup targets.
    """
    _local(client)
    task.check()
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("新模型名称只能使用小写字母、数字、点、横线和下划线（最多61字符）。")
    if not isinstance(base, str) or not base.strip() or _canonical(name) == _canonical(base):
        raise ValueError("个人模型必须使用新的名称，不能覆盖源模型。")
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("system"), str)
            or not snapshot["system"].strip() or snapshot.get("context") != context
            or isinstance(context, bool) or not isinstance(context, int) or context <= 0):
        raise ValueError("个人模型快照或上下文无效。")
    Budget(context).require(_messages(snapshot["system"]))
    rows = client.request("/api/tags", task=task).get("models", [])
    if any(_canonical(row.get("name", "")) == _canonical(name) for row in rows):
        raise ValueError("这个模型名称已存在，请使用新的个人模型名称。")
    source = next((row for row in rows if _canonical(row.get("name", "")) == _canonical(base)), None)
    if source is None:
        raise ValueError("本机未找到源模型，请先安装文字模型。")
    info = client.request("/api/show", {"model": base}, task)
    if _remote(source, base) or _remote(info, base):
        raise ValueError("云端模型不能创建本机个人记忆快照，请选择本地权重模型。")
    limits = [int(value) for key, value in info.get("model_info", {}).items()
              if key.endswith(".context_length") and isinstance(value, (int, float))
              and not isinstance(value, bool) and value > 0]
    if context > (min(limits) if limits else 4096):
        raise ValueError("源模型元数据不能确认所选上下文，请改用较小上下文。")
    task.check()
    system = snapshot["system"]
    started = False
    try:
        started = True
        result = client.request("/api/create", {"model": name, "from": base, "system": system,
            "parameters": {"num_ctx": context}, "stream": False}, task)
        if result.get("status") != "success":
            raise RuntimeError("Ollama 未确认个人模型创建成功。")
        task.check()
        created = client.request("/api/show", {"model": name}, task)
        if (created.get("system") != system or _context_value(created) != context
                or _remote(created, name)):
            raise RuntimeError("个人模型保存校验失败，尚未切换到新模型。")
        task.check()
        return name
    except Exception as exc:
        if started:
            try:
                _cleanup_created(client, name, system, context)
            except Exception:
                # Expose the orphan name without hiding the actual error. The
                # caller may surface it for manual inspection; no old model is
                # removed just to make the cleanup appear successful.
                exc.personal_model_cleanup_pending = name
        raise
