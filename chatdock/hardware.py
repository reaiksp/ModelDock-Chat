"""Conservative joint memory estimates; compression is not a benchmark guarantee."""
import os
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil
from .parameters import VARIANTS, parameter_size, download_gib, runtime_gib

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
VOICE_MODELS = {
    "0.6B": {"repo": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "gpu": 4.0, "ram": 4.0, "disk": 5.0},
    "1.7B": {"repo": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "gpu": 7.0, "ram": 5.0, "disk": 8.0},
}
# Download and runtime memory estimates at 4096 context, GiB.
TEXT_MODELS = {
    "chat": [("qwen3:0.6b", .5, 1.8), ("qwen3:1.7b", 1.31, 3),
             ("qwen3:4b", 2.33, 4.8), ("qwen3:8b", 4.85, 7.5),
             ("qwen3:14b", 8.67, 12.5), ("qwen3:32b", 18.63, 25)],
    "code": [("qwen2.5-coder:0.5b", .38, 1.6), ("qwen2.5-coder:1.5b", .92, 2.8),
             ("qwen2.5-coder:3b", 1.77, 4.4), ("qwen2.5-coder:7b", 4.38, 7),
             ("qwen2.5-coder:14b", 8.38, 12), ("qwen2.5-coder:32b", 18.63, 25)],
}


# Mixed-precision GGUFs retain the same 27B base. Full filenames exclude optional
# MTP heads and prevent an ambiguous quantization suffix from selecting one.
COMPRESSED_REPO = "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF"
COMPRESSED_REVISION = "d562806dbafae37109975e970aae91b43e73b440"
TEXT_METADATA = {}
TEXT_MODELS["compressed"] = []
for quant, size_bytes, memory, digest in [
    ("IQ2_XS", 8422841472, 10.0, "f0ae5006da0ce6225935339e4e989369f94de95d2263cf969519f8420c9ae02c"),
    ("IQ2_S", 9259510912, 10.8, "16c9802111aa9ef3acde465188d6d601f8db128ee3d828ad983a5caca4135ecb"),
    ("IQ3_XXS", 10094357632, 11.5, "fdfcb6a29b11188956dfbfd904223588a6c1b77eb250c3e8a36e1bd269df91f7"),
    ("IQ3_S", 11771546784, 13.0, "64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810"),
]:
    filename = "Qwen3.8-27B-GSQ-RCO-" + quant + ".gguf"
    tag = "hf.co/" + COMPRESSED_REPO + ":" + filename
    # Round upward; the catalog never understates a known download size.
    download = math.ceil(size_bytes / 1024**3 * 10) / 10
    TEXT_MODELS["compressed"].append((tag, download, memory))
    TEXT_METADATA[tag] = {"method": "GSQ + RCO", "base_model": "Qwen/Qwen3.8-27B",
        "parameters": "27B", "quantization": quant,
        "source": "https://huggingface.co/" + COMPRESSED_REPO,
        "source_revision": COMPRESSED_REVISION, "filename": filename,
        "size_bytes": size_bytes, "sha256": digest}


# The five slider positions keep the same base model and end at BF16 text weights.
# Legacy ungraded calls retain the original four GSQ/RCO choices above.
GRADE_LABELS = {1: "极致精简 · 约2 bit", 2: "轻量优先 · 约3 bit", 3: "均衡 · Q4_K_M",
                4: "高精度 · Q8_0", 5: "完全体 · F16 / BF16"}
CONVERSATION_CONTEXTS = {1: 4096, 2: 4096, 3: 8192, 4: 16384, 5: 32768}
CONVERSATION_RESERVES = {1: 0.0, 2: 0.0, 3: 1.5, 4: 4.0, 5: 8.0}
for data in TEXT_METADATA.values():
    data["compression_grade"] = {"IQ2_XS": 1, "IQ3_XXS": 2}.get(data["quantization"], 0)
GRADE_TEXT_MODELS = {data["compression_grade"]: row
                     for row in TEXT_MODELS["compressed"]
                     if (data := TEXT_METADATA[row[0]])["compression_grade"]}
for grade, quant, size_bytes, memory, digest in [
    (3, "Q4_K_M", 18973870528, 20.5, "c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747"),
    (4, "Q8_0", 28595763648, 30.0, "aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1"),
    (5, "BF16", 53808282048, 54.0, "222e1905eef352bae7c06f3d17dd9385bcb2ee96f435573d18457c7ad1002894"),
]:
    repo = "ggml-org/Qwen3.8-27B-GGUF"
    filename = "Qwen3.8-27B-" + quant + ".gguf"
    tag = "hf.co/" + repo + ":" + filename
    download = math.ceil(size_bytes / 1024**3 * 10) / 10
    GRADE_TEXT_MODELS[grade] = (tag, download, memory)
    TEXT_METADATA[tag] = {"method": "BF16 原精度" if grade == 5 else "GGUF 量化",
        "base_model": "Qwen/Qwen3.8-27B", "parameters": "27B", "quantization": quant,
        "compression_grade": grade, "source": "https://huggingface.co/" + repo,
        "source_revision": "a316ea527fde32c12487be7574e2db5ac2ad2a7e",
        "filename": filename, "size_bytes": size_bytes, "sha256": digest}


PARAMETER_TEXT_MODELS = {27.0: GRADE_TEXT_MODELS}
for row in VARIANTS:
    PARAMETER_TEXT_MODELS.setdefault(row['parameters'], {})[row['grade']] = (row['tag'], download_gib(row), runtime_gib(row))
    TEXT_METADATA[row['tag']] = {"method": "GGUF", "base_model": row['family'], "parameters": str(row['parameters']) + 'B',
        "parameter_size": row['parameters'], "compression_grade": row['grade'], "quantization": row['quant'],
        "source": "https://huggingface.co/" + row['repo'], "source_revision": row['revision'],
        "filename": row['filename'], "size_bytes": row['size'], "sha256": row['sha256']}


def _level(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 6):
        raise ValueError(field + "必须是 1–5 的整数。")
    return value


@dataclass
class Hardware:
    ram: float
    available: float
    vram: float
    disk: float
    model_disk: float
    gpu: str = "CPU"


def free_disk(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free / 1024**3


def ollama_models_dir():
    value = os.getenv("OLLAMA_MODELS")
    if not value and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "OLLAMA_MODELS")[0]
        except OSError:
            pass
    return Path(os.path.expandvars(value)) if value else Path.home() / ".ollama/models"


def detect(data_path):
    mem = psutil.virtual_memory()
    vram, gpu = 0.0, "CPU / unknown GPU"
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            result = subprocess.run([smi, "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, encoding="utf-8", timeout=10, creationflags=FLAGS, check=True)
            cards = []
            for line in result.stdout.splitlines():
                name, free = line.rsplit(",", 1)
                cards.append((float(free.strip()) / 1024, name.strip()))
            vram, gpu = cards[0]  # CUDA index 0, matching the managed speech runtime.
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return Hardware(mem.total / 1024**3, mem.available / 1024**3, vram,
                    free_disk(data_path), free_disk(ollama_models_dir()), gpu)


def recommend(hw, voice=False, size="auto", usage="chat", preference="balanced", same_disk=True,
              remote_text=False, capacity="large", compression_level=None, conversation_demand=None, parameter_limit=None):
    """Joint budgets; sliders never change providers or reduce requested context."""
    if compression_level is not None:
        _level(compression_level, "压缩档位")
    if conversation_demand is not None:
        _level(conversation_demand, "对话需求")
    selected_parameters = parameter_size(parameter_limit) if parameter_limit is not None else 27.0
    grade_models = PARAMETER_TEXT_MODELS[selected_parameters]
    graded = compression_level is not None and usage == "compressed" and not remote_text
    ram = max(0, min(hw.ram - max(3, hw.ram * .2), hw.available - 1))
    gpu = max(0, hw.vram - max(1, hw.vram * .12))
    sizes = ([size] if size in VOICE_MODELS else
             (["1.7B", "0.6B"] if preference == "quality" else ["0.6B", "1.7B"])) if voice else [None]
    context = (CONVERSATION_CONTEXTS[conversation_demand] if conversation_demand is not None
               else (16384 if capacity == "context" else 4096))
    extra = (CONVERSATION_RESERVES[conversation_demand] if conversation_demand is not None
             else (4.0 if context > 4096 else 0.0)) if not remote_text else 0.0
    if graded and selected_parameters != 27:
        extra *= 2  # Conservative context reserve for newly catalogued transformer families.
    if remote_text and conversation_demand is not None:
        context = None  # This plan covers local speech, never the provider's context setting.
    choices = ([("API", 0, 0)] if remote_text else
               [grade_models[level] for level in range(1, compression_level + 1) if level in grade_models] if graded else
               TEXT_MODELS.get(usage, TEXT_MODELS["chat"]))
    plans = []
    for variant in sizes:
        reserve = VOICE_MODELS[variant] if variant else {"gpu": 0, "ram": 0, "disk": 0}
        if variant and (gpu < reserve["gpu"] + (0 if remote_text else 1.6)
                        or ram < reserve["ram"] + (0 if remote_text else 2)
                        or hw.disk < reserve["disk"] + 12):
            continue
        use_gpu = gpu >= 1.6 and ram >= 2
        budget = (gpu - reserve["gpu"]) if use_gpu else ram
        if not use_gpu and preference != "quality":
            budget = min(budget, (3.2 if preference == "speed" else 5) + extra)
        for tag, download, memory in choices:
            required = download * (2.15 if tag in TEXT_METADATA else 1.15) + 1 + ((reserve["disk"] + 12) if variant and same_disk else 0)
            if memory + extra <= budget and (remote_text or hw.model_disk >= required):
                metadata = TEXT_METADATA.get(tag, {})
                effective = metadata.get("compression_grade", 0) if graded else 0
                fallback = ("所选档位无法同时满足文字、对话上下文、语音或磁盘预算，已推荐可容纳的较低档位。"
                            if graded and effective < compression_level else "")
                plans.append({"text": tag, "voice": variant, "memory": memory,
                    "budget": round(budget, 1), "context": context, "context_reserve": extra,
                    "reserve_gpu": reserve["gpu"], "reserve_ram": reserve["ram"],
                    "device": "cuda:0" if variant else None, "mode": "GPU" if use_gpu else "CPU",
                    "download": download, **metadata,
                    "compression_level": compression_level if graded else None,
                    "effective_compression_level": effective, "fallback_reason": fallback,
                    "conversation_demand": conversation_demand if not remote_text else None,
                    "parameter_limit": parameter_limit, "actual_parameters": selected_parameters if graded else None})
    if not plans:
        return None
    if graded:
        # The slider owns compression selection. Speed preference must not lower it again.
        top = max(p["effective_compression_level"] for p in plans)
        plans = [p for p in plans if p["effective_compression_level"] == top]
        preferred = "1.7B" if preference == "quality" else "0.6B"
        return max(plans, key=lambda p: p["voice"] == preferred)
    # Legacy callers keep the original TTS-first automatic preference semantics.
    if voice and size == "auto" and preference == "quality":
        preferred = [p for p in plans if p["voice"] == "1.7B"]
        plans = preferred or plans
    plans.sort(key=lambda p: (p["memory"], p["voice"] == "0.6B"))
    if preference == "speed":
        memories = sorted(set(p["memory"] for p in plans))
        chosen = memories[max(0, len(memories) - 2)]
        plans = [p for p in plans if p["memory"] == chosen]
    return plans[-1]
