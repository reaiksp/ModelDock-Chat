"""Opt-in real local synthesis smoke test; downloads only with --install."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatdock.network import Task
from chatdock.speech import SpeechService, readout_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--torch-wheel", default="")
    parser.add_argument("--size", choices=["0.6B", "1.7B"], default="1.7B")
    parser.add_argument("--source", choices=["huggingface", "modelscope"], default="huggingface")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    service = SpeechService(args.root)
    task = Task()
    log = lambda value: print(value, flush=True)
    original = "你好，这是 ModelDock Chat 的中文朗读测试。今天也要保持好心情。"
    try:
        if args.install:
            service.install(args.size, sys.executable, args.source, task, log, args.torch_wheel)
        health = service.start(args.size, task, log)
        print(json.dumps(health, ensure_ascii=False), flush=True)
        result = service.generate(readout_text(original), {"speaker": "Vivian", "language": "Chinese",
            "instruct": "温和、清晰地朗读。"}, args.root / "audio", task, lambda kind, value: log(str((kind, value))))
        report = dict(status="passed", repo=health["repo"], immutable_input=original,
                      result=result)
        (args.root / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2), "utf-8")
        print(json.dumps(report,ensure_ascii=False), flush=True)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
