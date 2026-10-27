import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from chatdock.store import Store
from chatdock.ui import STYLE, Window

app = QApplication([])
app.setStyleSheet(STYLE)
temporary = tempfile.TemporaryDirectory(prefix="modeldock-chat-ui-")
window = Window(Store(Path(temporary.name) / "test.db"), auto_connect=True)
phase = ["connect"]
started = time.monotonic()
result = {}

def poll():
    if time.monotonic() - started > 90:
        result.update(status="failed", error="UI integration timeout", detail=window.status.text())
        window.close()
        app.quit()
        return
    if not window.job and window.connected:
        if phase[0] == "connect":
            window.prompt.setPlainText("只回复两个字：你好")
            assert window.send_button.isEnabled()
            phase[0] = "chat"
            window.send()
        elif phase[0] == "chat":
            messages = window.store.messages(window.session_id)
            if messages and messages[-1]["role"] == "assistant":
                assert messages[-1]["content"].strip()
                window.prompt.setPlainText("中" * 5000)
                assert not window.send_button.isEnabled()
                assert "超限" in window.counter.text()
                result.update(status="passed", model=window.engine.model,
                    one_click_connection=True, streamed_chat=messages[-1]["content"],
                    input_limit_warning=True)
                window.close()
                app.quit()
                return
    QTimer.singleShot(100, poll)

QTimer.singleShot(300, poll)
app.exec()
output = Path(__file__).resolve().parents[1] / "artifacts/ui-integration-result.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
temporary.cleanup()
print(json.dumps(result, ensure_ascii=True), flush=True)
if result.get("status") != "passed":
    raise SystemExit(1)
