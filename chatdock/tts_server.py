"""Standalone Qwen3-TTS loopback bridge. Run using the isolated speech Python."""
import argparse
from collections import deque
import hmac
import io
import json
import os
import threading
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SPEAKERS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]
LANGUAGES = ["Auto", "Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"]
PROTOCOL = "modeldock-tts-v1"


class Runtime:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.lock = threading.Lock()
        self.events = {}
        self.cancelled_ids = deque(maxlen=128)
        self.events_lock = threading.Lock()

    def health(self):
        return {"protocol": PROTOCOL, "repo": self.config["repo"], "device": self.config["device"],
                "loaded": self.model is not None, "speakers": SPEAKERS, "languages": LANGUAGES}

    def cancel(self, ident):
        with self.events_lock:
            event = self.events.get(ident)
            if event:
                event.set()
            elif isinstance(ident, str) and 1 <= len(ident) <= 64:
                self.cancelled_ids.append(ident)

    def synthesize(self, body):
        text = body.get("text", "")
        ident = body.get("request_id", "")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 64:
            raise ValueError("Invalid request id")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("Text must contain 1-500 characters")
        speaker, language = body.get("speaker", "Vivian"), body.get("language", "Chinese")
        instruct = body.get("instruct", "")
        if speaker not in SPEAKERS or language not in LANGUAGES or not isinstance(instruct, str) or len(instruct) > 1000:
            raise ValueError("Invalid voice parameters")
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Speech service is busy")
        event = threading.Event()
        with self.events_lock:
            self.events[ident] = event
            if ident in self.cancelled_ids:
                event.set()
        try:
            if event.is_set():
                raise InterruptedError("Cancelled")
            import numpy as np
            import torch
            from transformers import StoppingCriteria, StoppingCriteriaList
            from qwen_tts import Qwen3TTSModel
            if self.model is None:
                self.model = Qwen3TTSModel.from_pretrained(self.config["model_path"],
                    device_map=self.config["device"], dtype=torch.float32 if self.config["device"] == "cpu" else torch.float16,
                    attn_implementation="sdpa", local_files_only=True)
            if event.is_set():
                raise InterruptedError("Cancelled")
            class Stop(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):
                    return event.is_set()
            kwargs = dict(text=text, language=language, speaker=speaker,
                max_new_tokens=2048, stopping_criteria=StoppingCriteriaList([Stop()]))
            if "1.7B" in self.config["repo"]:
                kwargs["instruct"] = instruct
            with torch.inference_mode():
                audio, rate = self.model.generate_custom_voice(**kwargs)
            if event.is_set():
                raise InterruptedError("Cancelled")
            value = np.asarray(audio[0], dtype=np.float32).reshape(-1)
            if not np.isfinite(value).all() or not 0 < len(value) <= rate * 170:
                raise ValueError("Invalid or truncated audio")
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes((np.clip(value, -1, 1) * 32767).astype("<i2").tobytes())
            return output.getvalue()
        finally:
            with self.events_lock:
                self.events.pop(ident, None)
            self.lock.release()


def serve(config, ready):
    runtime = Runtime(config)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *args):
            pass  # Do not log spoken text, tokens or chat content.
        def send(self, code, payload, content_type="application/json"):
            raw = json.dumps(payload).encode() if content_type == "application/json" else payload
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (OSError, ConnectionError):
                pass
        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + config["token"])
        def do_GET(self):
            if not self.authorized():
                return self.send(401, {"error": "Unauthorized"})
            if self.path != "/health":
                return self.send(404, {"error": "Not found"})
            self.send(200, runtime.health())
        def do_POST(self):
            if not self.authorized():
                return self.send(401, {"error": "Unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16000:
                    raise ValueError("Invalid body size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Invalid body")
                if self.path == "/speech":
                    return self.send(200, runtime.synthesize(body), "audio/wav")
                if self.path == "/cancel":
                    runtime.cancel(body.get("request_id", ""))
                    return self.send(200, {"cancelled": True})
                if self.path == "/shutdown":
                    with runtime.events_lock:
                        for event in runtime.events.values():
                            event.set()
                    self.send(200, {"stopping": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.send(404, {"error": "Not found"})
            except BlockingIOError:
                self.send(409, {"error": "Busy"})
            except InterruptedError:
                self.send(409, {"error": "Cancelled"})
            except (ValueError, TypeError, KeyError):
                self.send(400, {"error": "Invalid request or generated audio"})
            except Exception:
                traceback.print_exc()
                self.send(500, {"error": "Local synthesis failed; check model files, runtime and free memory"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint = dict(config, host="127.0.0.1", port=server.server_port)
    temp = ready.with_suffix(".tmp")
    temp.write_text(json.dumps(endpoint), "utf-8")
    temp.replace(ready)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--ready", required=True)
    args = parser.parse_args()
    # Inference must never silently download models or send telemetry.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    serve(json.loads(Path(args.config).read_text("utf-8")), Path(args.ready))

if __name__ == "__main__":
    main()
