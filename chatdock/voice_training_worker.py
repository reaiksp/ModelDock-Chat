"""Audio normalization and fine-tuned Qwen3-TTS evaluation worker.

Run this file with the isolated TTS Python environment, not the MDC UI venv.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


if __name__ == "__main__":
    # This file lives beside chatdock/secrets.py.  When run as a script that
    # directory is sys.path[0] and shadows the stdlib secrets module, which
    # breaks numpy.random import.  The worker does not need local imports.
    try:
        _here = Path(__file__).resolve().parent
        sys.path[:] = [item for item in sys.path
                       if Path(item or ".").resolve() != _here]
    except OSError:
        pass


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def normalize(args):
    import librosa
    import numpy as np
    import soundfile as sf

    source = Path(args.input)
    if not source.is_file():
        raise ValueError("输入音频不存在。")
    audio, sr = librosa.load(str(source), sr=None, mono=True)
    if audio.size == 0:
        raise ValueError("输入音频为空。")
    if args.trim:
        audio = librosa.effects.trim(audio, top_db=35)[0]
    if audio.size == 0:
        raise ValueError("裁剪静音后没有有效音频。")
    if sr != args.target_sr:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=args.target_sr)
        sr = args.target_sr
    # Check the real content duration before adding the 50 ms edge pads.
    content_duration = float(len(audio) / sr)
    if args.max_seconds and args.max_seconds > 0 and content_duration > args.max_seconds:
        raise ValueError(f"音频 {content_duration:.2f} 秒超过上限 {args.max_seconds} 秒，请切分后再加入。")
    pad = int(sr * 0.05)
    if pad:
        audio = np.pad(audio, (pad, pad), mode="constant")
    peak = float(np.max(np.abs(audio)))
    if not math.isfinite(peak) or peak < 1e-4:
        raise ValueError("音频音量过低或无效。")
    if args.peak > 0:
        audio = audio * (float(args.peak) / peak)
        peak = float(args.peak)
    rms = float(np.sqrt(np.mean(audio * audio)))
    silence = float(np.mean(np.abs(audio) < 0.005))
    clipped = float(np.mean(np.abs(audio) >= 0.999))
    duration = float(len(audio) / sr)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), np.clip(audio, -1.0, 1.0), sr, subtype="PCM_16")
    emit({"ok": True, "output": str(target), "sampleRate": sr, "duration": round(duration, 3),
          "peak": round(peak, 4), "rms": round(rms, 4),
          "silenceRatio": round(silence, 4), "clippingRatio": round(clipped, 4)})


def evaluate(args):
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    model_path = Path(args.model)
    if not model_path.is_dir():
        raise ValueError("微调模型目录不存在。")
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    model_type = str(getattr(getattr(model, "model", None), "tts_model_type", "") or "")
    if model_type != "custom_voice":
        raise ValueError("评估仅支持微调后的 custom_voice 模型。")
    kwargs = dict(text=args.text, language=args.language, speaker=args.speaker,
                  max_new_tokens=2048)
    if args.instruct:
        kwargs["instruct"] = args.instruct
    with torch.inference_mode():
        wavs, sr = model.generate_custom_voice(**kwargs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), wavs[0], sr, subtype="PCM_16")
    emit({"ok": True, "output": str(output), "sampleRate": sr,
          "duration": round(len(wavs[0]) / sr, 3), "modelType": model_type,
          "speaker": args.speaker})


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    n = sub.add_parser("normalize")
    n.add_argument("--input", required=True)
    n.add_argument("--output", required=True)
    n.add_argument("--target-sr", type=int, default=24000)
    n.add_argument("--peak", type=float, default=0.95)
    n.add_argument("--max-seconds", type=float, default=0.0)
    n.add_argument("--trim", action=argparse.BooleanOptionalAction, default=True)
    n.set_defaults(func=normalize)
    e = sub.add_parser("eval")
    e.add_argument("--model", required=True)
    e.add_argument("--output", required=True)
    e.add_argument("--text", required=True)
    e.add_argument("--speaker", required=True)
    e.add_argument("--language", default="Chinese")
    e.add_argument("--instruct", default="")
    e.set_defaults(func=evaluate)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
