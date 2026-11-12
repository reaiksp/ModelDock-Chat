"""Single-speaker Qwen3-TTS fine-tuning project manager.

The manager is deliberately independent of the GUI.  It owns project metadata,
audio normalization, dataset preparation, detached training supervision and
checkpoint validation.  Heavy work runs through the isolated Qwen TTS Python
environment; normal UI tests never load a model or start training.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - MDC declares psutil, fallback is defensive
    psutil = None


class VoiceTrainingError(RuntimeError):
    """A user-facing training error.  Messages never contain tokens."""


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _atomic_text(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    temp.write_text(text, "utf-8")
    temp.replace(path)


def _atomic_json(path: Path, value):
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _sha256(path: Path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _name(value, label="名称"):
    text = str(value or "").strip()
    if not re.fullmatch(r"[\w][\w.-]{0,63}", text, re.UNICODE) or text in {".", ".."}:
        raise VoiceTrainingError(f"{label}只能包含文字、数字、下划线、点和短横线，最多 64 个字符。")
    return text


def _speaker_name(value):
    return _name(value, "speaker 名称")


def _default_root():
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "ModelDockChat" / "speech" / "training"


def _default_tts_python():
    return _default_root().parent / "venv" / "Scripts" / "python.exe"


def _default_finetuning():
    from .storage import speech_models_dir
    return speech_models_dir(_default_root().parent) / "Qwen3-TTS-finetuning"


def _default_base_paths():
    from .storage import speech_models_dir
    root = speech_models_dir(_default_root().parent)
    return {"0.6b": root / "0.6B-Base", "1.7b": root / "1.7B-Base"}


def _creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _duration(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


class VoiceTrainingManager:
    """Manage a collection of local single-speaker training projects."""

    def __init__(self, root=None, active_project=None, tts_python=None,
                 finetuning_dir=None, base_paths=None, worker_script=None,
                 runner_script=None, normalizer=None, popen_factory=None):
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tts_python = Path(tts_python) if tts_python else _default_tts_python()
        self.finetuning_dir = Path(finetuning_dir) if finetuning_dir else _default_finetuning()
        self.base_paths = dict(base_paths or _default_base_paths())
        self.worker_script = Path(worker_script) if worker_script else Path(__file__).with_name("voice_training_worker.py")
        self.runner_script = Path(runner_script) if runner_script else Path(__file__).with_name("voice_training_runner.py")
        self.repo_root = Path(__file__).resolve().parents[1]
        self._normalizer = normalizer
        self._popen = popen_factory or subprocess.Popen
        self._run_process = subprocess.run
        self._runner_process = None
        self.default_project = "my_voice"
        self._active_path = self.root / "active.json"
        active = str(active_project).strip() if active_project else self._active_from_disk()
        self._active = _name(active or self.default_project, "项目名")
        if not self.project_file(self._active).is_file():
            self._create_project(self._active)
        if not self._active_path.is_file():
            self._write_active()

    # ---------- projects ----------

    def project_dir(self, project=None):
        return self.root / _name(project or self._active, "项目名")

    def project_file(self, project=None):
        return self.project_dir(project) / "project.json"

    @property
    def active_name(self):
        return self._active

    def _active_from_disk(self):
        value = _read_json(self._active_path, {})
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            try:
                return _name(value["name"], "项目名")
            except VoiceTrainingError:
                return self.default_project
        return self.default_project

    def _write_active(self):
        _atomic_json(self._active_path, {"name": self._active, "updated": time.time()})

    def _defaults(self, name):
        return {
            "version": 1,
            "name": name,
            "created": time.time(),
            "updated": time.time(),
            "speaker": name,
            "reference": None,
            "clips": [],
            "raw": {},
            "prepared": {"status": "missing"},
            "training": {"status": "idle"},
            "runs": [],
        }

    def _create_project(self, name):
        name = _name(name, "项目名")
        folder = self.root / name
        if self.project_file(name).is_file():
            raise VoiceTrainingError(f"项目已存在：{name}")
        for child in ("audio", "logs", "runs"):
            (folder / child).mkdir(parents=True, exist_ok=True)
        project = self._defaults(name)
        _atomic_json(folder / "project.json", project)
        return project

    def current_project(self, create=False):
        path = self.project_file()
        if not path.is_file():
            if not create:
                raise VoiceTrainingError("当前训练项目不存在；先运行 /train new <名称>。")
            self._create_project(self._active)
        project = _read_json(path)
        if not isinstance(project, dict):
            raise VoiceTrainingError("训练项目 JSON 损坏，请先备份并修复。")
        data = self._defaults(self._active)
        data.update(project)
        data["name"] = self._active
        data.setdefault("reference", None)
        data.setdefault("clips", [])
        data.setdefault("raw", {})
        data.setdefault("prepared", {"status": "missing"})
        data.setdefault("training", {"status": "idle"})
        data.setdefault("runs", [])
        return data

    def _save_project(self, project):
        project["updated"] = time.time()
        _atomic_json(self.project_file(project.get("name") or self._active), project)
        return project

    def create_project(self, name, activate=True):
        name = _name(name, "项目名")
        if self.project_file(name).is_file():
            raise VoiceTrainingError(f"项目已存在：{name}")
        project = self._create_project(name)
        if activate:
            self.use_project(name)
        return {"name": name, "path": str(self.project_dir(name)), "project": project}

    def use_project(self, name):
        name = _name(name, "项目名")
        if not self.project_file(name).is_file():
            raise VoiceTrainingError(f"项目不存在：{name}")
        self._active = name
        self._write_active()
        return self.current_project()

    def list_projects(self):
        rows = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_dir() or not (path / "project.json").is_file():
                continue
            data = _read_json(path / "project.json", {})
            if not isinstance(data, dict):
                continue
            clips = data.get("clips") if isinstance(data.get("clips"), list) else []
            total = sum(_duration(clip.get("duration")) for clip in clips if isinstance(clip, dict))
            training = data.get("training") if isinstance(data.get("training"), dict) else {}
            rows.append({
                "name": path.name,
                "active": path.name == self._active,
                "path": str(path),
                "clipCount": len(clips),
                "totalDuration": round(total, 3),
                "training": training.get("status") or "idle",
            })
        return rows

    def _project_abs(self, relative):
        return self.project_dir() / str(relative)

    def _invalidate_prepared(self, project, reason="dataset changed"):
        prepared = project.setdefault("prepared", {})
        if prepared.get("status") != "missing":
            prepared["status"] = "stale"
            prepared["invalidated"] = reason
            prepared["invalidatedAt"] = time.time()
        project["updated"] = time.time()

    def _ensure_idle(self):
        status = self.training_status()
        if status.get("status") == "running" and status.get("runnerAlive"):
            raise VoiceTrainingError("训练正在运行；先 /train stop，再修改数据集。")
        return status

    # ---------- audio ----------

    def _normalize_audio(self, source, output, max_seconds=0):
        source = Path(source).expanduser()
        output = Path(output)
        if not source.is_file():
            raise VoiceTrainingError(f"音频不存在：{source}")
        output.parent.mkdir(parents=True, exist_ok=True)
        if self._normalizer is not None:
            info = self._normalizer(str(source), str(output), max_seconds)
        else:
            info = self._worker_normalize(source, output, max_seconds)
        if not isinstance(info, dict):
            raise VoiceTrainingError("音频归一化返回格式无效。")
        if not output.is_file() or output.stat().st_size <= 44:
            raise VoiceTrainingError("音频归一化没有生成有效 WAV。")
        duration = _duration(info.get("duration"))
        if duration <= 0:
            raise VoiceTrainingError("音频归一化没有返回有效时长。")
        info = dict(info)
        info["duration"] = duration
        info["path"] = str(output)
        return info

    def _worker_normalize(self, source, output, max_seconds):
        if not self.tts_python.is_file():
            raise VoiceTrainingError(f"找不到 TTS Python：{self.tts_python}")
        if not self.worker_script.is_file():
            raise VoiceTrainingError("找不到 voice_training_worker.py。")
        command = [
            str(self.tts_python), str(self.worker_script), "normalize",
            "--input", str(source), "--output", str(output),
            "--target-sr", "24000", "--peak", "0.95",
            "--max-seconds", str(float(max_seconds)), "--trim",
        ]
        return self._worker_json(command, timeout=180)

    def _worker_json(self, command, timeout):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_OFFLINE": "1", "DO_NOT_TRACK": "1"})
        try:
            result = self._run_process(
                command, cwd=str(self.repo_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=_creationflags(), env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise VoiceTrainingError("音频 worker 超时。") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "worker failed")[-900:]
            raise VoiceTrainingError("音频 worker 失败：" + detail.strip())
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
        raise VoiceTrainingError("音频 worker 未返回 JSON。")

    def add_clip(self, wav, text):
        self._ensure_idle()
        text = str(text or "").strip()
        if not text:
            raise VoiceTrainingError("训练片段文本不能为空。")
        if len(text) > 2000:
            raise VoiceTrainingError("训练片段文本过长（最多 2000 字符）。")
        project = self.current_project(create=True)
        folder = self.project_dir()
        audio_dir = folder / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        temp = audio_dir / (".normalize-" + _stamp() + ".wav")
        try:
            info = self._normalize_audio(wav, temp, 0)
            seconds = _duration(info.get("duration"))
            if seconds < 0.8:
                raise VoiceTrainingError(f"片段只有 {seconds:.2f} 秒，至少需要 0.8 秒。")
            used = {int(clip.get("id", 0)) for clip in project["clips"] if isinstance(clip, dict)}
            ident = max(used, default=0) + 1
            target = audio_dir / f"clip_{ident:04d}.wav"
            while target.exists():
                ident += 1
                target = audio_dir / f"clip_{ident:04d}.wav"
            shutil.move(str(temp), str(target))
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        clip = {
            "id": ident,
            "audio": target.relative_to(folder).as_posix(),
            "text": text,
            "duration": round(seconds, 3),
            "sampleRate": info.get("sampleRate") or 24000,
            "peak": info.get("peak"),
            "rms": info.get("rms"),
        }
        project["clips"].append(clip)
        self._invalidate_prepared(project, "clip added")
        self._save_project(project)
        warning = ("片段超过 30 秒，建议切分成更短的片段以提高训练稳定性；不会拒绝。"
                   if seconds > 30 else "")
        return dict(clip, warning=warning, project=project["name"])

    def set_reference(self, wav, text=""):
        self._ensure_idle()
        text = str(text or "").strip()
        if len(text) > 2000:
            raise VoiceTrainingError("参考文本过长（最多 2000 字符）。")
        project = self.current_project(create=True)
        folder = self.project_dir()
        temp = folder / (".reference-" + _stamp() + ".wav")
        target = folder / "reference.wav"
        try:
            info = self._normalize_audio(wav, temp, 0)
            seconds = _duration(info.get("duration"))
            if seconds < 2.0:
                raise VoiceTrainingError(f"参考音频只有 {seconds:.2f} 秒，至少需要 2 秒（建议 10–30 秒）。")
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        project["reference"] = {
            "audio": "reference.wav",
            "text": text,
            "duration": round(seconds, 3),
            "sampleRate": info.get("sampleRate") or 24000,
            "peak": info.get("peak"),
            "rms": info.get("rms"),
        }
        self._invalidate_prepared(project, "reference changed")
        self._save_project(project)
        warnings = []
        if seconds < 10:
            warnings.append("参考音频少于 10 秒，建议 10–30 秒以提高音色稳定性。")
        if seconds > 30:
            warnings.append("参考音频超过 30 秒，建议切分；训练/推理会较慢，但不会拒绝。")
        return dict(project["reference"], warning="；".join(warnings), project=project["name"])

    def clips(self):
        return list(self.current_project().get("clips") or [])

    def remove_clip(self, index):
        self._ensure_idle()
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise VoiceTrainingError("片段编号必须是 1 开始的整数。")
        project = self.current_project(create=True)
        clips = project.get("clips") or []
        if not 1 <= index <= len(clips):
            raise VoiceTrainingError(f"片段编号超出范围：1–{len(clips)}。")
        removed = clips.pop(index - 1)
        path = self.project_dir() / removed.get("audio", "")
        if path.is_file():
            path.unlink()
        self._invalidate_prepared(project, "clip removed")
        self._save_project(project)
        return {"removed": removed, "remaining": len(clips)}

    def clear_clips(self):
        self._ensure_idle()
        project = self.current_project(create=True)
        folder = self.project_dir()
        count = 0
        for clip in project.get("clips") or []:
            path = folder / str(clip.get("audio") or "")
            if path.is_file():
                path.unlink(missing_ok=True)
                count += 1
        project["clips"] = []
        for name in ("train_raw.jsonl", "train_with_codes.jsonl"):
            (folder / name).unlink(missing_ok=True)
        project["raw"] = {}
        project["prepared"] = {"status": "missing"}
        self._save_project(project)
        return {"removed": count}

    # ---------- dataset ----------

    def build_raw(self):
        self._ensure_idle()
        project = self.current_project(create=True)
        reference = project.get("reference")
        if not isinstance(reference, dict) or not reference.get("audio"):
            raise VoiceTrainingError("还没有参考音频；先运行 /train ref <wav> | <参考文本>。")
        ref_path = self.project_dir() / str(reference["audio"])
        if not ref_path.is_file():
            raise VoiceTrainingError("参考音频文件丢失，请重新设置 /train ref。")
        clips = project.get("clips") or []
        if not clips:
            raise VoiceTrainingError("还没有训练片段；先运行 /train add <wav> | <文本>。")
        lines = []
        for clip in clips:
            audio_path = self.project_dir() / str(clip.get("audio") or "")
            if not audio_path.is_file():
                raise VoiceTrainingError(f"训练片段文件丢失：{audio_path.name}")
            lines.append(json.dumps({
                "audio": str(audio_path.resolve()),
                "text": str(clip.get("text") or ""),
                "ref_audio": str(ref_path.resolve()),
            }, ensure_ascii=False))
        raw_path = self.project_dir() / "train_raw.jsonl"
        _atomic_text(raw_path, "\n".join(lines) + "\n")
        digest = _sha256(raw_path)
        project["raw"] = {
            "path": "train_raw.jsonl",
            "sha256": digest,
            "count": len(lines),
            "at": time.time(),
        }
        prepared = project.setdefault("prepared", {})
        if prepared.get("rawSha256") != digest:
            if prepared.get("status") == "ready":
                prepared["status"] = "stale"
                prepared["invalidated"] = "raw jsonl changed"
            prepared["rawSha256"] = digest
        self._save_project(project)
        return {"path": str(raw_path), "sha256": digest, "count": len(lines), "project": project["name"]}

    def _tokenizer_path(self, base):
        base_path = self.base_paths.get(base.lower())
        if not base_path:
            raise VoiceTrainingError("未知模型规格；只支持 0.6b 或 1.7b。")
        candidate = Path(base_path) / "speech_tokenizer"
        if not candidate.is_dir():
            raise VoiceTrainingError(f"本地 tokenizer 不存在：{candidate}")
        return candidate

    def prepare_codes(self, base="0.6b"):
        self._ensure_idle()
        base = str(base or "0.6b").lower()
        if base not in {"0.6b", "1.7b"}:
            raise VoiceTrainingError("只支持 0.6b 或 1.7b。")
        raw_info = self.build_raw()
        project = self.current_project(create=True)
        tokenizer = self._tokenizer_path(base)
        prepared_path = self.project_dir() / "train_with_codes.jsonl"
        prepared = project.setdefault("prepared", {})
        same = (
            prepared.get("status") == "ready"
            and prepared.get("rawSha256") == raw_info["sha256"]
            and prepared.get("base") == base
            and prepared.get("tokenizer") == str(tokenizer)
            and prepared_path.is_file()
        )
        if same:
            expected = prepared.get("preparedSha256")
            if not expected or _sha256(prepared_path) == expected:
                return dict(prepared, path=str(prepared_path), count=prepared.get("count"), cached=True)
        prepare_script = self.finetuning_dir / "prepare_data.py"
        if not prepare_script.is_file():
            raise VoiceTrainingError(f"找不到官方 prepare_data.py：{prepare_script}")
        if not self.tts_python.is_file():
            raise VoiceTrainingError(f"找不到 TTS Python：{self.tts_python}")
        log_path = self.project_dir() / "logs" / f"prepare-{_stamp()}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.tts_python), str(prepare_script), "--device", "cuda:0",
            "--tokenizer_model_path", str(tokenizer),
            "--input_jsonl", str(self.project_dir() / "train_raw.jsonl"),
            "--output_jsonl", str(prepared_path),
        ]
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_OFFLINE": "1", "DO_NOT_TRACK": "1"})
        started = time.time()
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                result = self._run_process(
                    command, cwd=str(self.finetuning_dir), stdout=log,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, timeout=3600, creationflags=_creationflags(),
                )
            if result.returncode != 0:
                raise VoiceTrainingError(f"prepare_data.py 退出码 {result.returncode}")
            rows = []
            for line in prepared_path.read_text("utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
            if len(rows) != raw_info["count"]:
                raise VoiceTrainingError(f"codes 行数不一致：{len(rows)} / {raw_info['count']}")
            if any(not isinstance(row.get("audio_codes"), list) or not row["audio_codes"] for row in rows):
                raise VoiceTrainingError("codes 文件包含空 audio_codes。")
            prepared_sha = _sha256(prepared_path)
            prepared = {
                "status": "ready",
                "path": "train_with_codes.jsonl",
                "rawSha256": raw_info["sha256"],
                "preparedSha256": prepared_sha,
                "base": base,
                "tokenizer": str(tokenizer),
                "count": len(rows),
                "log": str(log_path),
                "at": time.time(),
                "durationSec": round(time.time() - started, 2),
            }
            project["prepared"] = prepared
            self._save_project(project)
            return dict(prepared, path=str(prepared_path), cached=False)
        except Exception as exc:
            project["prepared"] = {
                "status": "failed",
                "rawSha256": raw_info["sha256"],
                "base": base,
                "tokenizer": str(tokenizer),
                "log": str(log_path),
                "error": str(exc)[:600],
                "at": time.time(),
            }
            self._save_project(project)
            raise VoiceTrainingError(f"提取 codes 失败：{exc}；日志：{log_path}") from exc

    # ---------- training ----------

    def _run_dir(self):
        folder = self.project_dir() / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        run_id = _stamp()
        target = folder / run_id
        suffix = 1
        while target.exists():
            suffix += 1
            target = folder / f"{run_id}-{suffix}"
        target.mkdir(parents=True)
        return target

    def start_training(self, base="0.6b", epochs=3, batch_size=1, lr=2e-6,
                       speaker=None, gradient_accumulation_steps=1,
                       gradient_checkpointing=True):
        self._ensure_idle()
        base = str(base or "0.6b").lower()
        if base not in {"0.6b", "1.7b"}:
            raise VoiceTrainingError("只支持 0.6b 或 1.7b。")
        try:
            epochs, batch_size = int(epochs), int(batch_size)
            gradient_accumulation_steps = int(gradient_accumulation_steps)
            lr = float(lr)
        except (TypeError, ValueError):
            raise VoiceTrainingError("训练参数必须是数字。")
        if not 1 <= epochs <= 50:
            raise VoiceTrainingError("epochs 必须在 1–50。")
        if not 1 <= batch_size <= 16:
            raise VoiceTrainingError("batch 必须在 1–16。")
        if not 1 <= gradient_accumulation_steps <= 16:
            raise VoiceTrainingError("gradient accumulation 必须在 1–16。")
        if not 0 < lr <= 1e-3:
            raise VoiceTrainingError("lr 必须大于 0 且不超过 1e-3。")
        base_path = Path(self.base_paths[base])
        if not (base_path / "config.json").is_file():
            raise VoiceTrainingError(f"Base 模型不存在：{base_path}")
        model_config = _read_json(base_path / "config.json", {})
        if model_config.get("tts_model_type") not in (None, "base"):
            raise VoiceTrainingError("训练需选择 Base 模型，不能使用 CustomVoice 或 VoiceDesign。")
        actual_size = str(model_config.get("tts_model_size") or "")
        if actual_size and actual_size not in ({"0b6", "0.6b", "0.6B"} if base == "0.6b" else {"1b7", "1.7b", "1.7B"}):
            raise VoiceTrainingError("所选 Base 大小与模型配置不一致。")
        project = self.current_project(create=True)
        speaker = _speaker_name(speaker or project.get("speaker") or project["name"])
        project["speaker"] = speaker
        prepared = self.prepare_codes(base)
        prepared_path = Path(prepared["path"])
        if not prepared_path.is_file():
            raise VoiceTrainingError("codes 文件不存在，请重新 /train prepare。")
        run_dir = self._run_dir()
        output_dir = run_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "train.log"
        runner_log = run_dir / "runner.log"
        done_path = run_dir / "done.json"
        sft_script = self.finetuning_dir / "sft_12hz_win.py"
        windows_script = sft_script.is_file()
        if not windows_script:
            sft_script = self.finetuning_dir / "sft_12hz.py"
        if not sft_script.is_file():
            raise VoiceTrainingError(f"找不到训练脚本：{sft_script}")
        if not self.runner_script.is_file():
            raise VoiceTrainingError("找不到 voice_training_runner.py。")
        if not self.tts_python.is_file():
            raise VoiceTrainingError(f"找不到 TTS Python：{self.tts_python}")
        train_command = [
            str(self.tts_python), str(sft_script),
            "--init_model_path", str(base_path),
            "--output_model_path", str(output_dir),
            "--train_jsonl", str(prepared_path),
            "--batch_size", str(batch_size),
            "--lr", repr(lr),
            "--num_epochs", str(epochs),
            "--speaker_name", speaker,
        ]
        if windows_script:
            train_command += ["--gradient_accumulation_steps", str(gradient_accumulation_steps)]
        if gradient_checkpointing and windows_script:
            train_command.append("--gradient_checkpointing")
        command = [
            str(self.tts_python), str(self.runner_script),
            "--done-json", str(done_path),
            "--log", str(log_path),
            "--cwd", str(self.finetuning_dir),
            "--",
        ] + train_command
        run_meta = {
            "run": run_dir.name,
            "base": base,
            "speaker": speaker,
            "epochs": epochs,
            "batchSize": batch_size,
            "lr": lr,
            "gradientAccumulationSteps": gradient_accumulation_steps,
            "gradientCheckpointing": bool(gradient_checkpointing),
            "preparedPath": str(prepared_path),
            "rawSha256": prepared.get("rawSha256"),
            "preparedSha256": prepared.get("preparedSha256"),
            "outputDir": str(output_dir),
            "logPath": str(log_path),
            "donePath": str(done_path),
            "started": time.time(),
        }
        _atomic_json(run_dir / "run.json", run_meta)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_OFFLINE": "1", "DO_NOT_TRACK": "1"})
        detached = 0
        if os.name == "nt":
            detached = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            with runner_log.open("a", encoding="utf-8", buffering=1) as stream:
                process = self._popen(
                    command, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, env=env,
                    creationflags=detached,
                )
        except Exception as exc:
            training = dict(run_meta, status="failed", error=str(exc)[:600],
                            finished=time.time())
            project["training"] = training
            self._save_project(project)
            raise VoiceTrainingError(f"启动训练进程失败：{exc}") from exc
        self._runner_process = process
        training = dict(
            run_meta,
            status="running",
            runnerPid=int(getattr(process, "pid", 0) or 0),
            runnerLogPath=str(runner_log),
        )
        project["training"] = training
        project["runs"] = (project.get("runs") or []) + [run_meta]
        self._save_project(project)
        warning = ""
        if base == "1.7b":
            warning = "1.7B 训练显存/内存风险高，建议只保留训练进程并先暂停 TTS/网关。"
        return dict(training, warning=warning)

    def _runner_alive(self, pid):
        try:
            pid = int(pid or 0)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if self._runner_process is not None and getattr(self._runner_process, "pid", None) == pid:
            return self._runner_process.poll() is None
        if psutil is None:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        try:
            process = psutil.Process(pid)
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return False
            try:
                command = " ".join(process.cmdline()).casefold()
                if "voice_training_runner.py" not in command:
                    return False
            except (psutil.Error, OSError):
                pass
            return True
        except (psutil.Error, OSError):
            return False

    def _read_done(self, done_path):
        if not done_path:
            return None
        value = _read_json(Path(done_path))
        if not isinstance(value, dict):
            return None
        return value

    def training_status(self):
        project = self.current_project(create=True)
        training = dict(project.get("training") or {})
        done_path = training.get("donePath")
        done = self._read_done(done_path)
        previous = training.get("status") or "idle"
        if done and done.get("exitCode") is not None:
            code = int(done["exitCode"])
            training["exitCode"] = code
            training["status"] = "completed" if code == 0 else "failed"
            training["finished"] = done.get("finished") or time.time()
            training["durationSec"] = done.get("durationSec")
            training["runnerAlive"] = False
            if done.get("error"):
                training["error"] = str(done["error"])[:600]
        else:
            alive = self._runner_alive(training.get("runnerPid"))
            training["runnerAlive"] = bool(alive)
            if alive:
                training["status"] = "running"
            elif previous == "running":
                training["status"] = "stopped"
                training["finished"] = time.time()
                training["stopReason"] = "runner process not found"
            else:
                training["status"] = previous
        training["checkpoints"] = self.checkpoints()
        project["training"] = training
        self._save_project(project)
        return dict(training)

    def tail_log(self, lines=40):
        try:
            lines = max(1, min(500, int(lines)))
        except (TypeError, ValueError):
            raise VoiceTrainingError("日志行数必须是整数。")
        project = self.current_project(create=True)
        training = project.get("training") or {}
        log_path = Path(training.get("logPath") or "")
        if not training.get("logPath"):
            candidates = sorted(
                (self.project_dir() / "runs").glob("*/train.log"),
                key=lambda item: item.stat().st_mtime, reverse=True,
            )
            log_path = candidates[0] if candidates else self.project_dir() / "train.log"
        if not log_path.is_file():
            return ""
        content = log_path.read_text("utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])

    def _kill_tree(self, pid):
        if not pid:
            return False
        if psutil is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, creationflags=_creationflags())
                return True
            try:
                os.kill(int(pid), 9)
                return True
            except OSError:
                return False
        try:
            parent = psutil.Process(int(pid))
        except psutil.Error:
            return False
        children = []
        try:
            children = parent.children(recursive=True)
        except psutil.Error:
            pass
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.Error:
                pass
        try:
            parent.terminate()
        except psutil.Error:
            pass
        gone, alive = psutil.wait_procs(children + [parent], timeout=5)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
        return True

    def stop_training(self):
        status = self.training_status()
        pid = status.get("runnerPid")
        if not status.get("runnerAlive") and status.get("status") != "running":
            return status
        killed = self._kill_tree(pid)
        project = self.current_project(create=True)
        training = project.get("training") or {}
        training["status"] = "stopped"
        training["runnerAlive"] = False
        training["finished"] = time.time()
        training["stopReason"] = "stopped by user" if killed else "runner not found"
        project["training"] = training
        self._save_project(project)
        return dict(training, checkpoints=self.checkpoints())

    # ---------- checkpoints / evaluation / deploy ----------

    def _checkpoint_meta(self, path: Path):
        config_path = path / "config.json"
        config = _read_json(config_path, {})
        if not isinstance(config, dict):
            config = {}
        talker = config.get("talker_config") if isinstance(config.get("talker_config"), dict) else {}
        speakers = list((talker.get("spk_id") or {}).keys()) if isinstance(talker.get("spk_id"), dict) else []
        run_meta = _read_json(path.parent.parent / "run.json", {}) if path.parent.name == "output" else {}
        if not isinstance(run_meta, dict):
            run_meta = {}
        base = str(run_meta.get("base") or ("1.7b" if "1.7B" in str(path) else "0.6b"))
        return {
            "name": path.name,
            "path": str(path),
            "run": path.parent.parent.name if path.parent.name == "output" else path.parent.name,
            "mtime": config_path.stat().st_mtime if config_path.is_file() else path.stat().st_mtime,
            "modelType": config.get("tts_model_type"),
            "speakers": speakers,
            "base": base,
        }

    def checkpoints(self):
        folder = self.project_dir()
        candidates = []
        for pattern in ("output/checkpoint-*", "runs/*/output/checkpoint-*"):
            candidates.extend(folder.glob(pattern))
        rows = []
        for path in sorted({item.resolve() for item in candidates if item.is_dir()}):
            if not (path / "config.json").is_file():
                continue
            rows.append(self._checkpoint_meta(path))
        rows.sort(key=lambda row: (row["mtime"], row["path"]), reverse=True)
        for index, row in enumerate(rows, 1):
            row["index"] = index
        return rows

    def resolve_checkpoint(self, query=None):
        rows = self.checkpoints()
        if query is not None and str(query).strip():
            value = str(query).strip().strip('"').strip("'")
            path = Path(value).expanduser()
            if path.is_dir() and (path / "config.json").is_file():
                return path.resolve()
        if not rows:
            raise VoiceTrainingError("还没有可用的 checkpoint；先完成一次训练。")
        if query is not None and str(query).strip():
            value = str(query).strip().strip('"').strip("'")
            if value.isdigit():
                index = int(value)
                if not 1 <= index <= len(rows):
                    raise VoiceTrainingError(f"checkpoint 编号超出范围：1–{len(rows)}。")
                return Path(rows[index - 1]["path"])
            exact = [row for row in rows if value in {
                row["name"], row["path"], f"{row['run']}/{row['name']}",
            }]
            if len(exact) == 1:
                return Path(exact[0]["path"])
            folded = value.casefold()
            partial = [row for row in rows if folded in row["name"].casefold()
                       or folded in row["path"].casefold() or folded in row["run"].casefold()]
            if len(partial) == 1:
                return Path(partial[0]["path"])
            if len(partial) > 1:
                raise VoiceTrainingError("checkpoint 名称匹配到多个结果，请用 /train checkpoints 的编号。")
            raise VoiceTrainingError(f"找不到 checkpoint：{value}")
        return Path(rows[0]["path"])

    def deploy_plan(self, checkpoint=None, speaker=None):
        status = self.training_status()
        if status.get("status") == "running" and status.get("runnerAlive"):
            raise VoiceTrainingError("训练正在运行，不能部署 checkpoint。")
        path = self.resolve_checkpoint(checkpoint)
        config = _read_json(path / "config.json", {})
        if not isinstance(config, dict):
            raise VoiceTrainingError("checkpoint 缺少有效 config.json。")
        if config.get("tts_model_type") != "custom_voice":
            raise VoiceTrainingError("checkpoint 不是 custom_voice，不能用于单说话人部署。")
        talker = config.get("talker_config") if isinstance(config.get("talker_config"), dict) else {}
        speakers = talker.get("spk_id") if isinstance(talker.get("spk_id"), dict) else {}
        if not speakers:
            raise VoiceTrainingError("checkpoint 的 talker_config.spk_id 为空。")
        requested = str(speaker or "").strip()
        if requested:
            if requested not in speakers:
                raise VoiceTrainingError(f"speaker {requested!r} 不在 checkpoint 的 spk_id 中。")
            selected = requested
        elif len(speakers) == 1:
            selected = next(iter(speakers))
        else:
            raise VoiceTrainingError("checkpoint 有多个 speaker，请用 --speaker 指定。")
        if not (path / "model.safetensors").is_file():
            raise VoiceTrainingError("checkpoint 缺少 model.safetensors。")
        run_meta = _read_json(path.parent.parent / "run.json", {}) if path.parent.name == "output" else {}
        base = str((run_meta or {}).get("base") or ("1.7b" if "1.7B" in str(path) else "0.6b"))
        base_label = "1.7B" if base == "1.7b" else "0.6B"
        repo = f"ModelDock/Qwen3-TTS-12Hz-{base_label}-Finetuned"
        return {
            "checkpoint": str(path),
            "checkpointName": path.name,
            "speaker": selected,
            "modelPath": str(path),
            "repo": repo,
            "base": base,
            "extraSpeakers": [selected],
        }

    def evaluate(self, checkpoint=None, text="", speaker=None, language="Chinese", instruct=""):
        status = self.training_status()
        if status.get("status") == "running" and status.get("runnerAlive"):
            raise VoiceTrainingError("训练正在运行，先停止后再评估。")
        text = str(text or "").strip()
        if not text:
            raise VoiceTrainingError("评估文本不能为空。")
        plan = self.deploy_plan(checkpoint, speaker)
        if not self.tts_python.is_file():
            raise VoiceTrainingError(f"找不到 TTS Python：{self.tts_python}")
        if not self.worker_script.is_file():
            raise VoiceTrainingError("找不到 voice_training_worker.py。")
        out_dir = self.project_dir() / "evaluations"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"eval-{_stamp()}.wav"
        command = [
            str(self.tts_python), str(self.worker_script), "eval",
            "--model", plan["modelPath"], "--output", str(output),
            "--text", text, "--speaker", plan["speaker"],
            "--language", str(language or "Chinese"),
        ]
        if instruct:
            command += ["--instruct", str(instruct)]
        info = self._worker_json(command, timeout=1800)
        if not output.is_file():
            raise VoiceTrainingError("评估 worker 没有生成 WAV。")
        return dict(info, checkpoint=plan["checkpoint"], output=str(output),
                    speaker=plan["speaker"], project=self._active)

    def status(self):
        project = self.current_project(create=True)
        training = self.training_status()
        clips = project.get("clips") or []
        total = sum(_duration(clip.get("duration")) for clip in clips if isinstance(clip, dict))
        reference = project.get("reference")
        ref_info = dict(reference) if isinstance(reference, dict) else None
        if ref_info and ref_info.get("audio"):
            ref_info["path"] = str(self.project_dir() / ref_info["audio"])
        return {
            "project": project["name"],
            "path": str(self.project_dir()),
            "speaker": project.get("speaker") or project["name"],
            "clipCount": len(clips),
            "duration": round(total, 3),
            "clips": clips,
            "reference": ref_info,
            "raw": project.get("raw") or {},
            "prepared": project.get("prepared") or {"status": "missing"},
            "training": training,
            "checkpoints": training.get("checkpoints") or self.checkpoints(),
        }

def _voice_training_guard(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except VoiceTrainingError:
            raise
        except Exception as exc:
            raise VoiceTrainingError(str(exc)) from exc
    return wrapped


for _public_name in (
    "create_project", "use_project", "list_projects", "add_clip", "set_reference",
    "clips", "remove_clip", "clear_clips", "build_raw", "prepare_codes",
    "start_training", "training_status", "tail_log", "stop_training",
    "checkpoints", "resolve_checkpoint", "deploy_plan", "evaluate", "status",
):
    setattr(VoiceTrainingManager, _public_name,
            _voice_training_guard(getattr(VoiceTrainingManager, _public_name)))
del _public_name
