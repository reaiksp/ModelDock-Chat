import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

LOCAL_URL = "http://127.0.0.1:11434"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Cancelled(Exception):
    pass


class Task:
    def __init__(self):
        self.event = threading.Event()
        self.connection = None
        self.socket = None
        self.process = None

    def check(self):
        if self.event.is_set():
            raise Cancelled("操作已停止。")

    def cancel(self):
        self.event.set()
        process = self.process
        if process and process.poll() is None:
            try:
                import psutil
                parent = psutil.Process(process.pid)
                for child in reversed(parent.children(recursive=True)):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                process.terminate()
            except (OSError, psutil.Error):
                pass
        conn = self.connection
        sock = self.socket or (conn.sock if conn else None)
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def validate_url(base):
    parsed = urlsplit(base.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API 地址必须是完整的 HTTP(S) Base URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 不应包含账号密码、查询参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("外部提供商须使用 HTTPS；HTTP 仅允许本机回环地址。")
    _ = parsed.port
    return parsed


class Client:
    def __init__(self, base=LOCAL_URL, key=""):
        self.base = base.strip().rstrip("/")
        self.parsed = validate_url(self.base)
        if "\r" in key or "\n" in key:
            raise ValueError("API Key 包含无效换行。")
        self.key = key.strip()

    def open(self, path, body, task, timeout=180):
        task.check()
        cls = http.client.HTTPSConnection if self.parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(self.parsed.hostname, self.parsed.port, timeout=timeout)
        task.connection = conn
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        try:
            conn.connect()
            task.socket = conn.sock
            task.check()
            conn.request("GET" if body is None else "POST", self.parsed.path.rstrip("/") + path,
                         None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"), headers)
            task.check()
            response = conn.getresponse()
            if response.status >= 300:
                # Never echo a remote error body: it can reflect API keys or user data.
                status = response.status
                raise RuntimeError(f"API 返回 HTTP {status}。请检查地址、模型名、权限与配额。")
            return conn, response
        except Exception:
            conn.close()
            task.connection = None
            task.socket = None
            task.check()
            raise

    def request(self, path, body=None, task=None, timeout=180):
        task = task or Task()
        conn = None
        try:
            conn, response = self.open(path, body, task, timeout)
            raw = response.read(8_000_001)
            task.check()
            if len(raw) > 8_000_000:
                raise RuntimeError("API 响应过大。")
            return json.loads(raw)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            task.check()
            raise RuntimeError("API 连接或响应解析失败，请检查本地服务、网络与提供商配置。") from exc
        finally:
            if conn:
                conn.close()
            task.connection = None
            task.socket = None

    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        body = {"model": model, "messages": messages, "stream": True, "think": False,
                "options": {"num_ctx": context, "num_predict": reply}}
        if json_mode:
            body["format"] = "json"
        conn = None
        answer = ""
        try:
            conn, response = self.open("/api/chat", body, task)
            while True:
                task.check()
                line = response.readline(1_000_001)
                if not line:
                    raise RuntimeError("生成连接提前结束。已生成内容已保留，可重新发送。")
                if len(line) > 1_000_000:
                    raise RuntimeError("API 响应行过大。")
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError("本地模型生成失败，请检查模型是否已安装及可用内存。")
                token = data.get("message", {}).get("content", "")
                answer += token
                if token:
                    emit(token)
                if data.get("done"):
                    if data.get("done_reason") == "length":
                        emit("\n[达到本轮回复长度上限，可发送“继续”]")
                        answer += "\n[达到本轮回复长度上限，可发送“继续”]"
                    return answer
        except (OSError, ValueError, http.client.HTTPException) as exc:
            task.check()
            raise RuntimeError("生成连接失败，请检查 Ollama 服务后重试。") from exc
        finally:
            if conn:
                conn.close()
            task.connection = None
            task.socket = None

    def completion(self, model, messages, task, reply=1024):
        result = self.request("/chat/completions", {"model": model, "messages": messages,
                              "max_tokens": reply, "stream": False}, task)
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("学习输出被提供商截断；请缩小学习材料或增加输出预算。")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("提供商返回空内容；请核对模型或提供商的推理配置。")
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("提供商响应不符合 OpenAI 兼容聊天格式。") from exc


class OpenAIClient(Client):
    """Shared Chat Completions adapter for chat, summaries, memories and learning."""
    def __init__(self, config):
        super().__init__(config["base"], config.get("key", ""))
        self.stream = bool(config.get("stream", True))
        self.token_field = config.get("token_field", "max_tokens")
        self.reasoning = config.get("reasoning", "")
        if self.token_field not in {"max_tokens", "max_completion_tokens"} or self.reasoning not in {"", "none", "low", "medium", "high"}:
            raise ValueError("输出长度或推理参数无效。")

    def body(self, model, messages, reply, stream):
        body = {"model": model, "messages": messages, self.token_field: reply, "stream": stream}
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        return body

    @staticmethod
    def extract(result):
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("供应商返回错误，请检查模型、权限与配额。")
        try:
            choice = result["choices"][0]
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("提供商返回空正文；请检查推理配置或增加回复预算。")
            return content, choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("提供商响应不符合 Chat Completions 格式。") from exc

    def completion(self, model, messages, task, reply=1024):
        content, reason = self.extract(self.request("/chat/completions", self.body(model, messages, reply, False), task))
        if reason == "length":
            raise RuntimeError("结构化输出被截断，请缩小材料后重试。")
        return content

    def chat(self, model, messages, task, emit, context=4096, reply=768, json_mode=False):
        if json_mode or not self.stream:
            content, reason = self.extract(self.request("/chat/completions", self.body(model, messages, reply, False), task))
            if reason == "length":
                if json_mode:
                    raise RuntimeError("结构化输出被截断，请缩小材料后重试。")
                content += "\n[达到本轮回复长度上限，可发送“继续”]"
            task.check()
            emit(content)
            return content
        conn = None
        answer, finish, frame = "", None, []
        try:
            conn, response = self.open("/chat/completions", self.body(model, messages, reply, True), task)
            if "application/json" in response.getheader("Content-Type", "").lower():
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise RuntimeError("提供商响应过大。")
                task.check()
                answer, finish = self.extract(json.loads(raw))
                emit(answer)
            else:
                total = 0
                while True:
                    task.check()
                    raw = response.readline(1_000_001)
                    total += len(raw)
                    if len(raw) > 1_000_000 or total > 8_000_000:
                        raise RuntimeError("提供商响应过大。")
                    line = raw.decode("utf-8-sig").rstrip("\r\n")
                    if raw and line:
                        if line.startswith("data:"):
                            frame.append(line[5:].lstrip(" "))
                        continue
                    if frame:
                        payload, frame = "\n".join(frame), []
                        if payload == "[DONE]":
                            if not answer.strip():
                                raise RuntimeError("提供商返回空正文。")
                            break
                        data = json.loads(payload)
                        if not isinstance(data, dict) or data.get("error"):
                            raise RuntimeError("供应商流式生成失败，已生成内容已保留。")
                        choice = next((c for c in (data.get("choices") or []) if c.get("index", 0) == 0), None)
                        if choice:
                            token = (choice.get("delta") or {}).get("content") or ""
                            if not isinstance(token, str):
                                raise RuntimeError("供应商流式正文格式无效。")
                            if token:
                                answer += token
                                emit(token)
                            finish = choice.get("finish_reason") or finish
                    if not raw:
                        if finish is None:
                            raise RuntimeError("供应商流式连接提前结束，已生成内容已保留。")
                        break
            task.check()
            if not answer.strip():
                raise RuntimeError("提供商返回空正文。")
            if finish == "length":
                note = "\n[达到本轮回复长度上限，可发送“继续”]"
                answer += note
                emit(note)
            return answer
        except (OSError, ValueError, UnicodeError, http.client.HTTPException) as exc:
            task.check()
            raise RuntimeError("供应商连接或流式响应解析失败，请检查 API 配置。") from exc
        finally:
            if conn:
                conn.close()
            task.connection = None
            task.socket = None


def connect_local(task, log, data_path):
    client = Client()
    try:
        client.request("/api/version", task=task, timeout=3)
    except RuntimeError:
        task.check()
        cli = shutil.which("ollama")
        if not cli:
            candidate = Path(os.getenv("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
            cli = str(candidate) if candidate.is_file() else None
        if not cli:
            raise RuntimeError("未找到 Ollama。请安装 Ollama，再在 MDC 的语音与部署页面下载模型，或复用 ModelDock。")
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        if not env.get("OLLAMA_MODELS") and os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    env["OLLAMA_MODELS"] = str(winreg.QueryValueEx(key, "OLLAMA_MODELS")[0])
            except OSError:
                pass
        log("正在启动本机 Ollama CLI…")
        with (Path(data_path) / "ollama-server.log").open("ab") as stream:
            subprocess.Popen([cli, "serve"], env=env, stdin=subprocess.DEVNULL,
                             stdout=stream, stderr=stream, creationflags=FLAGS)
        for _ in range(30):
            task.check()
            try:
                client.request("/api/version", task=task, timeout=1)
                break
            except RuntimeError:
                task.event.wait(.5)
        else:
            raise RuntimeError("本机服务未就绪，请检查 Ollama 后重试。")
    models = client.request("/api/tags", task=task, timeout=10).get("models", [])
    models = [m for m in models if not m.get("remote_host") and not m.get("remote_model") and ":cloud" not in m.get("name", "")]
    parent = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "ModelDock/settings.json"
    selected = ""
    try:
        settings = json.loads(parent.read_text("utf-8"))
        selected = settings.get("model", "") if isinstance(settings, dict) else ""
    except (OSError, ValueError):
        pass
    return {"models": [m["name"] for m in models], "parent_model": selected}


def model_context(client, model, task):
    info = client.request("/api/show", {"model": model}, task)
    sizes = [int(v) for k, v in info.get("model_info", {}).items() if k.endswith(".context_length") and isinstance(v, (int, float)) and v > 0]
    return min(sizes) if sizes else 4096


def create_variant(client, base, name, system, task, context):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,60}", name):
        raise ValueError("新模型名称只能使用小写字母、数字、点、横线和下划线。")
    names = {m["name"].split(":")[0] for m in client.request("/api/tags", task=task).get("models", [])}
    if name in names or name == base.split(":")[0]:
        raise ValueError("这个模型名称已存在，请换一个新名称，避免覆盖。")
    result = client.request("/api/create", {"model": name, "from": base, "system": system,
        "parameters": {"num_ctx": context}, "stream": False}, task)
    if result.get("status") != "success":
        raise RuntimeError("Ollama 未确认派生模型创建成功。")
    return name
