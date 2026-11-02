"""Verified GSQ/RCO GGUF download followed by local Ollama CLI import.

Only the fixed audited files below are accepted. This path downloads the text
GGUF without a vision projector and never disables Ollama's network policy.
"""
import hashlib
import http.client
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from types import SimpleNamespace

REPO = "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF"
REVISION = "d562806dbafae37109975e970aae91b43e73b440"
FILES = {}
for quant, size, digest in [
    ("IQ2_XS", 8422841472, "f0ae5006da0ce6225935339e4e989369f94de95d2263cf969519f8420c9ae02c"),
    ("IQ2_S", 9259510912, "16c9802111aa9ef3acde465188d6d601f8db128ee3d828ad983a5caca4135ecb"),
    ("IQ3_XXS", 10094357632, "fdfcb6a29b11188956dfbfd904223588a6c1b77eb250c3e8a36e1bd269df91f7"),
    ("IQ3_S", 11771546784, "64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810"),
]:
    filename = "Qwen3.8-27B-GSQ-RCO-" + quant + ".gguf"
    tag = "hf.co/" + REPO + ":" + filename
    FILES[tag.casefold()] = {"tag": tag, "filename": filename, "size": size,
        "sha256": digest, "revision": REVISION, "repo": REPO}


# Higher-precision text weights of the same base; fixed, non-MTP files.
for filename, size, digest in [
    ("Qwen3.8-27B-Q4_K_M.gguf", 18973870528, "c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747"),
    ("Qwen3.8-27B-Q8_0.gguf", 28595763648, "aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1"),
    ("Qwen3.8-27B-BF16.gguf", 53808282048, "222e1905eef352bae7c06f3d17dd9385bcb2ee96f435573d18457c7ad1002894"),
]:
    repo = "ggml-org/Qwen3.8-27B-GGUF"
    tag = "hf.co/" + repo + ":" + filename
    FILES[tag.casefold()] = {"tag": tag, "filename": filename, "size": size,
        "sha256": digest, "revision": "a316ea527fde32c12487be7574e2db5ac2ad2a7e", "repo": repo}


from .parameters import VARIANTS
for row in VARIANTS:
    FILES[row["tag"].casefold()] = {k: row[k] for k in ("tag", "filename", "size", "sha256", "revision", "repo")}


def is_supported(tag):
    return isinstance(tag, str) and tag.casefold() in FILES


def _spec(tag):
    if not is_supported(tag):
        raise ValueError("仅支持已核对的量化或 BF16 完整文件标签。")
    return FILES[tag.casefold()]


def _request(path, body, task):
    task.check()
    conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=10)
    task.connection = conn
    try:
        conn.connect()
        if hasattr(task, "socket"):
            task.socket = conn.sock
        conn.request("GET" if body is None else "POST", path,
            None if body is None else json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read(2_000_001)
        task.check()
        if response.status != 200 or len(raw) > 2_000_000:
            raise RuntimeError("无法核对本机 Ollama 模型，请先连接本地服务。")
        return json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        task.check()
        raise RuntimeError("无法核对本机 Ollama 模型，请先连接本地服务。") from exc
    finally:
        conn.close()
        if getattr(task, "connection", None) is conn:
            task.connection = None
        if hasattr(task, "socket"):
            task.socket = None


def _source_digest(modelfile):
    if not isinstance(modelfile, str):
        return None
    rows = re.findall(r'(?mi)^\s*FROM[ \t]+(?:"([^"\r\n]+)"|([^\r\n]+))\s*$', modelfile)
    if len(rows) != 1:
        return None
    path = (rows[0][0] or rows[0][1]).strip()
    match = re.search(r'(?:^|[\\/])sha256[-:]([0-9a-fA-F]{64})$', path)
    return match.group(1).lower() if match else None


def _existing(spec, task):
    rows = _request("/api/tags", None, task).get("models", [])
    row = next((r for r in rows if r.get("name", "").casefold() == spec["tag"].casefold()), None)
    if row is None:
        return None
    if row.get("remote_host") or row.get("remote_model"):
        raise ValueError("同名模型属于远端模型，未覆盖；请先在 Ollama 中处理。")
    info = _request("/api/show", {"model": row["name"]}, task)
    if _source_digest(info.get("modelfile")) != spec["sha256"]:
        raise ValueError("同名模型权重不同或无法核对，未覆盖；请先在 Ollama 中处理。")
    return row["name"]


def _hash_file(path, task):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            task.check()
            digest.update(block)
    task.check()
    return digest.hexdigest()


def _verified(path, spec, task):
    return (path.is_file() and path.stat().st_size == spec["size"]
            and _hash_file(path, task) == spec["sha256"])


def _official_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.port in {None, 443}
            and not parsed.username and not parsed.password
            and (host == "huggingface.co" or host.endswith(".huggingface.co")
                 or host == "hf.co" or host.endswith(".hf.co")))


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _official_url(newurl):
            raise RuntimeError("官方模型下载跳转到未支持的地址，已停止。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _progress(callback, percent, message):
    if callback:
        callback(percent, message)


def _download(spec, path, task, log, progress):
    task.check()
    partial = path.with_name(path.name + ".part")
    url = "https://huggingface.co/" + spec["repo"] + "/resolve/" + spec["revision"] + "/" + spec["filename"] + "?download=true"
    downloaded, digest = 0, hashlib.sha256()
    if partial.is_file() and 0 < partial.stat().st_size < spec["size"]:
        # This prefix is not trusted: the complete file must still match SHA-256.
        with partial.open("rb") as existing:
            while block := existing.read(1024 * 1024):
                task.check()
                digest.update(block)
                downloaded += len(block)
        log("读取已有下载分片；完成后仍校验完整 SHA-256。")
    headers = {"User-Agent": "ModelDock-verified-GGUF"}
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_OfficialRedirect())
    log("从官方固定版本下载 GGUF；支持断点续传并校验完整文件。")
    connection = None
    try:
        with opener.open(request, timeout=30) as response:
            if not _official_url(response.geturl()):
                raise RuntimeError("官方模型下载地址无效，已停止。")
            mode = "wb"
            if response.status == 206 and downloaded:
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if not match or tuple(map(int, match.groups())) != (downloaded, spec["size"] - 1, spec["size"]):
                    raise RuntimeError("断点续传范围与已核对版本不符，已停止。")
                mode = "ab"
            elif response.status == 200:
                # A server without Range support supplies the whole file.
                downloaded, digest = 0, hashlib.sha256()
            else:
                raise RuntimeError("官方模型下载响应异常，请稍后重试。")
            length = response.headers.get("Content-Length")
            if length and int(length) != spec["size"] - downloaded:
                raise RuntimeError("下载文件大小与已核对版本不符，已停止。")
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            connection = SimpleNamespace(sock=sock)
            task.connection = connection
            if hasattr(task, "socket"):
                task.socket = sock
            task.check()
            with partial.open(mode) as output:
                while block := response.read(512 * 1024):
                    task.check()
                    if downloaded + len(block) > spec["size"]:
                        raise RuntimeError("下载内容超过已核对文件大小，已停止。")
                    output.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    _progress(progress, min(80, int(downloaded * 80 / spec["size"])),
                        f"GGUF 已下载 {downloaded / 1024**3:.2f} / {spec['size'] / 1024**3:.2f} GiB")
            task.check()
        if downloaded != spec["size"] or digest.hexdigest() != spec["sha256"]:
            raise RuntimeError("GGUF 大小或 SHA-256 校验失败，未导入；请重新下载。")
        partial.replace(path)
        log("GGUF 大小与 SHA-256 校验通过。")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        task.check()
        raise RuntimeError("官方 GGUF 下载失败；请检查网络后重试，已有分片将保留。") from exc
    finally:
        if getattr(task, "connection", None) is connection:
            task.connection = None
        if hasattr(task, "socket"):
            task.socket = None


def _import(spec, cli, path, cache, task, log):
    task.check()
    model_path = path.resolve().as_posix()
    if any(c in model_path for c in ['"', '\r', '\n']):
        raise ValueError("模型缓存路径包含无法安全导入的字符，请更换缓存目录。")
    descriptor, filename = tempfile.mkstemp(prefix="modeldock-gguf-", suffix=".Modelfile", dir=cache)
    modelfile = Path(filename)
    proc = None
    reader = None
    output = deque(maxlen=16)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('FROM "' + model_path + '"\n')
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["NO_COLOR"] = "1"
        log("通过 Ollama CLI 导入已校验的本地 GGUF…")
        proc = subprocess.Popen([str(cli), "create", spec["tag"], "-f", str(modelfile)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        task.process = proc
        def read_output():
            try:
                while line := proc.stdout.readline(2048):
                    output.append(line[:500])
            except (OSError, ValueError):
                pass
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + 300
        while proc.poll() is None:
            task.check()
            if time.monotonic() > deadline:
                raise RuntimeError("GGUF 本地导入超时，请检查磁盘和 Ollama 服务后重试。")
            task.event.wait(.1)
        task.check()
        reader.join(timeout=1)
        for line in list(output):
            if not re.search(r"https?://|authorization|token=|signature=", line, re.I):
                cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
                if cleaned:
                    log(cleaned[:300])
        if proc.returncode != 0:
            raise RuntimeError(f"GGUF 本地导入失败（退出码 {proc.returncode}），原有模型未主动删除。")
    except OSError as exc:
        task.check()
        raise RuntimeError("无法运行 Ollama CLI，请检查安装路径。") from exc
    finally:
        if proc:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            if reader:
                reader.join(timeout=1)
            if proc.stdout:
                proc.stdout.close()
            if getattr(task, "process", None) is proc:
                task.process = None
        modelfile.unlink(missing_ok=True)


def install_gguf(tag, cli, cache_dir, task, log, progress=None):
    """Download only an audited text GGUF, verify it, then import using the CLI.

    A verified cache can be reused. Failed downloads remain as .part for
    inspection; retries resume with Range and verify the complete SHA-256. Import and cache use two copies
    of the weights; callers must reserve disk space for both. Runtime loading is
    deliberately left to the caller's actual model-load verification.
    """
    spec = _spec(tag)
    task.check()
    existing = _existing(spec, task)
    if existing:
        log("同名本地模型的权重哈希已核对，复用现有模型。")
        _progress(progress, 99, "已核对现有压缩模型，准备验证实际加载…")
        return existing
    cache = Path(cache_dir).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / spec["filename"]
    if _verified(path, spec, task):
        log("复用已通过 SHA-256 校验的 GGUF 缓存。")
    elif path.exists():
        raise ValueError("已有 GGUF 缓存未通过校验，未覆盖；请检查该文件后重试。")
    else:
        _download(spec, path, task, log, progress)
    task.check()
    # Recheck immediately before import so a newly created different model is
    # refused rather than overwritten. The CLI has no atomic create-if-absent.
    existing = _existing(spec, task)
    if existing:
        return existing
    _progress(progress, 85, "文件校验通过，正在通过 CLI 导入本地模型…")
    _import(spec, cli, path, cache, task, log)
    task.check()
    actual = _existing(spec, task)
    if actual is None:
        raise RuntimeError("导入命令结束，但未核对到目标模型；请刷新并检查 Ollama。")
    _progress(progress, 99, "本地模型已导入，等待实际加载验证…")
    return actual
