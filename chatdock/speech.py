"""Local speech lifecycle, immutable readout adaptation and audio records."""
import array
import http.client
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

from .hardware import VOICE_MODELS
from .network import Cancelled, Client, FLAGS, Task

SPEAKERS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]
LANGUAGES = ["Auto", "Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"]


def readout_text(original, pronunciations="", skip_code=True):
    """Make a derived copy only. Pronunciation lines are literal term=reading pairs."""
    text = original
    if skip_code:
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"```[\s\S]*$", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://[^\s<>]+", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}|>|[-*+] |\d+\. )\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    mappings = {}
    for line in pronunciations.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError("发音词典每行使用：词语=朗读方式。")
        term, reading = line.split("=", 1)
        term, reading = term.strip(), reading.strip()
        if not term or not reading or len(term) > 80 or len(reading) > 160:
            raise ValueError("发音词典条目无效或过长。")
        mappings[term] = reading
    if len(mappings) > 500:
        raise ValueError("发音词典最多 500 条。")
    if mappings:
        pattern = re.compile("|".join(re.escape(t) for t in sorted(mappings, key=len, reverse=True)))
        text = pattern.sub(lambda m: mappings[m.group()], text)
    return re.sub(r"[ \t]+", " ", text).strip()


def chunks(text, limit=220):
    if not text.strip():
        raise ValueError("没有可朗读的正文。")
    if len(text) > 30000:
        raise ValueError("朗读文本超过 30000 字，请选择部分文字。")
    pieces, current = [], ""
    for part in re.split(r"(?<=[。！？!?；;\n])", text):
        while part:
            space = limit - len(current)
            current += part[:space]
            part = part[space:]
            if len(current) == limit or (current.strip() and not part):
                if current.strip():
                    pieces.append(current.strip())
                current = ""
    if current.strip():
        pieces.append(current.strip())
    return pieces


def inspect_wav(raw):
    with wave.open(io.BytesIO(raw), "rb") as wav:
        frames, rate, channels, width = wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        if width != 2 or channels != 1 or not 8000 <= rate <= 96000 or not 0 < frames <= rate * 180:
            raise ValueError("语音服务返回的音频格式无效。")
        data = wav.readframes(frames)
        if len(data) != frames * 2:
            raise ValueError("语音服务返回的音频不完整。")
    samples = array.array("h", data)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(x) for x in samples) / 32768
    rms = math.sqrt(sum((x / 32768)**2 for x in samples) / len(samples))
    silence = sum(abs(x) < 328 for x in samples) / len(samples)
    clipped = sum(abs(x) >= 32700 for x in samples) / len(samples)
    return {"seconds": round(frames / rate, 2), "peak": round(peak, 4), "rms": round(rms, 4),
            "silence_ratio": round(silence, 4), "clipping_ratio": round(clipped, 4),
            "warnings": [name for name, yes in [("near_silence", rms < .003),
                ("excess_silence", silence > .8), ("clipping", clipped > .01)] if yes]}


def run_command(args, task, log, cwd=None, env=None):
    """Argument arrays only. File-backed output avoids pipe deadlocks and console windows."""
    from tempfile import TemporaryFile
    task.check()
    with TemporaryFile(mode="w+b") as output:
        process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT,
            cwd=cwd, env=env, creationflags=FLAGS)
        task.process = process
        try:
            while process.poll() is None:
                if task.event.wait(.2):
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    task.check()
            task.check()
            if process.returncode:
                output.seek(max(0, output.tell() - 3000))
                detail = output.read().decode("utf-8", "replace")[-3000:]
                raise RuntimeError("本地命令失败，请检查网络、Python 与磁盘空间。\n" + detail)
        finally:
            if process.poll() is None:
                process.terminate()
            task.process = None
    log("本地命令完成。")


def find_python(explicit=""):
    if explicit:
        result = Path(explicit).expanduser().resolve()
        if not result.is_file():
            raise ValueError("请选择有效的 Python 3.12 可执行文件。")
        return str(result)
    if not getattr(sys, "frozen", False):
        return sys.executable
    launcher = shutil.which("py")
    if launcher:
        result = subprocess.run([launcher, "-3.12", "-c", "import sys;print(sys.executable)"],
            capture_output=True, text=True, timeout=10, creationflags=FLAGS)
        if result.returncode == 0 and Path(result.stdout.strip()).is_file():
            return result.stdout.strip()
    raise ValueError("未找到 Python 3.12，请安装后选择 python.exe。")


class SpeechService:
    def __init__(self, root):
        self.root = Path(root) / "speech"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ready = self.root / "endpoint.json"
        self.process = None
        self.request_id = None
        self.owned = False

    @property
    def python(self):
        return self.root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def endpoint(self):
        data = json.loads(self.ready.read_text("utf-8"))
        if data.get("host") != "127.0.0.1" or not isinstance(data.get("port"), int) or not 1 <= data["port"] <= 65535:
            raise ValueError("Invalid local speech endpoint")
        if not isinstance(data.get("token"), str) or len(data["token"]) < 32:
            raise ValueError("Invalid local speech token")
        return data

    def client(self):
        endpoint = self.endpoint()
        return Client("http://127.0.0.1:" + str(endpoint["port"]), endpoint["token"])

    def health(self):
        result = self.client().request("/health", timeout=2)
        if result.get("protocol") != "modeldock-tts-v1":
            raise ValueError("Unsupported local speech service")
        return result

    def install(self, size, python, source, task, log, torch_wheel=""):
        if size not in VOICE_MODELS or source not in {"huggingface", "modelscope"}:
            raise ValueError("Invalid speech model or download source")
        try:
            self.health()
        except (OSError, ValueError, RuntimeError):
            pass
        else:
            raise ValueError("请先停止语音服务，再安装或更换语音模型。")
        bootstrap = find_python(python)
        log("正在创建独立 Python 环境…")
        run_command([bootstrap, "-c", "import sys;assert sys.version_info[:2] == (3,12), 'Python 3.12 required'"], task, log)
        if not self.python.is_file():
            run_command([bootstrap, "-m", "venv", str(self.root / "venv")], task, log)
        log("正在安装 CUDA 12.8 PyTorch 与语音依赖，首次下载可能较大…")
        if torch_wheel:
            wheel = Path(torch_wheel).expanduser().resolve()
            if not wheel.is_file() or wheel.name != "torch-2.11.0+cu128-cp312-cp312-win_amd64.whl":
                raise ValueError("本地 PyTorch 包须为 torch 2.11.0 CUDA 12.8 / Python 3.12 / Windows x64。")
            run_command([self.python, "-m", "pip", "install", wheel], task, log)
        run_command([self.python, "-m", "pip", "install", "torch==2.11.0", "torchaudio==2.11.0",
            "--index-url", "https://download.pytorch.org/whl/cu128"], task, log)
        run_command([self.python, "-m", "pip", "install", "qwen-tts==0.1.1", "modelscope==1.34.0"], task, log)
        log("正在下载 Qwen3-TTS 模型，可取消并在下次重试…")
        script = Path(__file__).with_name("tts_download.py")
        run_command([self.python, script, "--root", self.root, "--size", size, "--source", source], task, log)
        return size

    def start(self, size, task, log):
        if size not in VOICE_MODELS:
            raise ValueError("Invalid speech model")
        try:
            health = self.health()
        except (OSError, ValueError, RuntimeError):
            health = None
        if health:
            if health.get("repo") == VOICE_MODELS[size]["repo"]:
                return health
            raise ValueError("请先停止当前语音服务，再更换模型。")
        model_path = self.root / "models" / size
        if not self.python.is_file() or not (model_path / "model.complete").is_file():
            raise ValueError("请先下载并安装选定的语音模型。")
        # The server gets a local model path and cannot download during inference.
        config = {"repo": VOICE_MODELS[size]["repo"], "model_path": str(model_path),
                  "device": "cuda:0", "token": secrets.token_urlsafe(32)}
        config_path = self.root / "launch.json"
        config_path.write_text(json.dumps(config), "utf-8")
        if self.ready.exists():
            self.ready.unlink()
        bridge = self.root / "tts_server.py"
        shutil.copy2(Path(__file__).with_name("tts_server.py"), bridge)
        log("正在启动本地语音服务，首次朗读时加载模型…")
        log_path = self.root / "service.log"
        with log_path.open("wb") as output:
            self.process = subprocess.Popen([str(self.python), str(bridge), "--config", str(config_path),
                "--ready", str(self.ready)], stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS)
        self.owned = True
        for _ in range(100):
            if task.event.is_set():
                self.stop()
            task.check()
            if self.process.poll() is not None:
                raise RuntimeError("本地语音服务启动失败，请检查独立环境与服务日志。")
            try:
                return self.health()
            except (OSError, ValueError, RuntimeError):
                task.event.wait(.1)
        raise RuntimeError("语音服务启动超时。")

    def stop(self):
        try:
            self.client().request("/shutdown", {}, timeout=2)
        except (OSError, ValueError, RuntimeError):
            pass
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                task = Task()
                task.process = self.process
                task.cancel()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        self.owned = False

    def cancel(self):
        ident = self.request_id
        if ident:
            try:
                self.client().request("/cancel", {"request_id": ident}, timeout=2)
            except (OSError, ValueError, RuntimeError):
                pass

    def generate(self, text, settings, folder, task, emit):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        metrics, paths = [], []
        parts = chunks(text)
        for index, part in enumerate(parts):
            task.check()
            ident = uuid.uuid4().hex
            self.request_id = ident
            client = self.client()
            conn = None
            start = time.monotonic()
            try:
                body = {"text": part, "request_id": ident, "speaker": settings["speaker"],
                    "language": settings["language"], "instruct": settings.get("instruct", "")}
                conn, response = client.open("/speech", body, task, timeout=600)
                raw = response.read(35_000_001)
                task.check()
                if len(raw) > 35_000_000 or "audio/wav" not in response.getheader("Content-Type", ""):
                    raise ValueError("语音服务返回的音频格式无效。")
                quality = inspect_wav(raw)
                quality["generation_seconds"] = round(time.monotonic() - start, 2)
                quality["segment"] = index + 1
                path = folder / (str(index + 1) + ".wav")
                path.write_bytes(raw)
                paths.append(str(path))
                metrics.append(quality)
                emit("speech_progress", (index + 1, len(parts), str(path), quality))
            except Exception as exc:
                self.cancel()
                task.check()
                if isinstance(exc, RuntimeError):
                    raise RuntimeError("本地语音生成失败，请检查空闲显存、语音环境和 service.log。") from exc
                raise
            finally:
                self.request_id = None
                if conn:
                    conn.close()
                task.connection = None
                task.socket = None
        return {"paths": paths, "metrics": metrics}


class SpeechRecords:
    def __init__(self, store):
        self.store = store
        self.store.execute("""CREATE TABLE IF NOT EXISTS speech_runs(
            id TEXT PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL, original TEXT NOT NULL,
            readout TEXT NOT NULL, config TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'running', created REAL NOT NULL)""")
        self.store.execute("""CREATE TABLE IF NOT EXISTS speech_reviews(
            run_id TEXT PRIMARY KEY REFERENCES speech_runs(id) ON DELETE CASCADE,
            accuracy INTEGER NOT NULL CHECK(accuracy BETWEEN 1 AND 10),
            naturalness INTEGER NOT NULL CHECK(naturalness BETWEEN 1 AND 10),
            clarity INTEGER NOT NULL CHECK(clarity BETWEEN 1 AND 10),
            summary TEXT NOT NULL, analysis TEXT NOT NULL)""")
        # A previous process may have ended mid-generation. Preserve its record, mark interrupted.
        self.store.execute("UPDATE speech_runs SET status='interrupted' WHERE status='running'")

    def begin(self, session_id, message_id, original, readout, config):
        ident = uuid.uuid4().hex
        self.store.execute("INSERT INTO speech_runs(id,session_id,message_id,original,readout,config,created) VALUES (?,?,?,?,?,?,?)",
            (ident, session_id, message_id, original, readout, json.dumps(config, ensure_ascii=False), time.time()))
        return ident

    def finish(self, ident, result, status="complete"):
        self.store.execute("UPDATE speech_runs SET result=?,status=? WHERE id=?", (json.dumps(result), status, ident))

    def review(self, ident, accuracy, naturalness, clarity, summary, analysis):
        if any(type(v) is not int or not 1 <= v <= 10 for v in (accuracy, naturalness, clarity)):
            raise ValueError("语音评分必须为 1–10 的整数。")
        if not summary.strip() or len(summary) > 2000 or len(analysis) > 8000:
            raise ValueError("请填写评价概述，概述或分析不能过长。")
        self.store.execute("INSERT INTO speech_reviews VALUES (?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET accuracy=excluded.accuracy,naturalness=excluded.naturalness,clarity=excluded.clarity,summary=excluded.summary,analysis=excluded.analysis",
            (ident, accuracy, naturalness, clarity, summary, analysis))
