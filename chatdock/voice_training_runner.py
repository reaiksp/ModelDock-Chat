"""Small detached supervisor for a fine-tuning job.

Writes a done JSON file so the interactive console can recover the exit code
after its own process or the job has finished.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    temp.replace(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--done-json", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--cwd", required=True)
    args, rest = parser.parse_known_args()
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        raise SystemExit("missing training command")
    started = time.time()
    result = {"started": started, "finished": None, "exitCode": None, "command": rest}
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8", buffering=1) as log:
            log.write(f"\n=== supervisor start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            process = subprocess.Popen(rest, cwd=args.cwd, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env=os.environ.copy())
            code = process.wait()
            result["exitCode"] = int(code)
            result["finished"] = time.time()
            result["durationSec"] = round(result["finished"] - started, 2)
            log.write(f"\n=== supervisor exit {code} ===\n")
    except BaseException as exc:
        result["exitCode"] = 1
        result["finished"] = time.time()
        result["durationSec"] = round(result["finished"] - started, 2)
        result["error"] = str(exc)
    atomic_json(args.done_json, result)
    return 0 if result.get("exitCode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
