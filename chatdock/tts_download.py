"""Executed only in the isolated TTS environment; downloads known official repos."""
import argparse
import json
from pathlib import Path

REPOS = {size: "Qwen/Qwen3-TTS-12Hz-" + size + "-CustomVoice" for size in ["0.6B", "1.7B"]}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--size", choices=list(REPOS), required=True)
    parser.add_argument("--source", choices=["huggingface", "modelscope"], required=True)
    args = parser.parse_args()
    target = Path(args.root) / "models" / args.size
    target.mkdir(parents=True, exist_ok=True)
    marker = target / "model.complete"
    marker.unlink(missing_ok=True)
    if args.source == "modelscope":
        from modelscope import snapshot_download
        snapshot_download(REPOS[args.size], local_dir=str(target))
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(REPOS[args.size], local_dir=str(target))
    if not (target / "config.json").is_file() or not list(target.rglob("*.safetensors")):
        raise RuntimeError("Model download incomplete")
    marker.write_text(json.dumps({"repo": REPOS[args.size], "source": args.source}), "utf-8")

if __name__ == "__main__":
    main()
