"""Named providers; credentials never enter metadata."""
import re
import uuid
from .network import validate_url


def validate_provider(config):
    name = str(config.get("name", "")).strip()
    model = str(config.get("model", "")).strip()
    base = str(config.get("base", "")).strip().rstrip("/")
    ident = config.get("id") or uuid.uuid4().hex[:16]
    if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident):
        raise ValueError("供应商 ID 无效。")
    if not name or not model or len(name) > 80 or len(model) > 200:
        raise ValueError("请填写有效的供应商名称和模型 ID。")
    validate_url(base)
    context = config.get("context", 4096)
    if isinstance(context, bool) or not isinstance(context, int) or not 2048 <= context <= 262144:
        raise ValueError("供应商上下文请填写 2048–262144 的整数。")
    token_field = config.get("token_field", "max_tokens")
    reasoning = config.get("reasoning", "")
    if token_field not in {"max_tokens", "max_completion_tokens"} or reasoning not in {"", "none", "low", "medium", "high"}:
        raise ValueError("输出长度或推理参数无效。")
    return {"id": ident, "name": name, "base": base, "model": model, "context": context,
            "stream": bool(config.get("stream", True)), "token_field": token_field, "reasoning": reasoning}


def load_providers(store):
    rows = store.get("providers_v2")
    if rows is None:
        base, model = store.get("provider_base", ""), store.get("provider_model", "")
        rows = [{"id": "legacy-learning", "name": "原学习 API", "base": base,
                 "model": model, "context": store.get("provider_context", 8192)}] if base and model else []
    if not isinstance(rows, list):
        return []
    valid, seen = [], set()
    for item in rows:
        try:
            item = validate_provider(item)
            if item["id"] not in seen:
                valid.append(item)
                seen.add(item["id"])
        except (ValueError, TypeError, AttributeError):
            continue
    return valid


def save_providers(store, profiles):
    store.set("providers_v2", [validate_provider(p) for p in profiles])
