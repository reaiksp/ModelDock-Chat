import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from chatdock.store import Store
from chatdock.ui import STYLE, Window

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
temporary = tempfile.TemporaryDirectory(prefix="modeldock-chat-preview-")
store = Store(Path(temporary.name) / "preview.db")
ident = store.new_session(title="一个更懂你的本地助手")
store.add_message(ident, "user", "以后叫我小林吧。我喜欢简洁的回答。还有，你知道 YYDS 是什么意思吗？")
store.add_message(ident, "assistant", "好的，小林。YYDS 是“永远的神”的缩写，通常用来表达赞赏。\n\n我会按你喜欢的方式，尽量简洁地回答。你的偏好会先整理为记忆候选，确认后再长期保存。")
store.add_memory("用户喜欢简洁的回答", evidence="我喜欢简洁的回答", source_session=ident)
store.add_knowledge("YYDS", "永远的神，用来表达赞赏", "这波操作真是 YYDS。", status="approved")
store.set("last_session", ident)
window = Window(store, auto_connect=False)
window.engine.model = "qwen3:4b"
window.model_combo.blockSignals(True)
window.model_combo.addItem("qwen3:4b")
window.model_combo.setCurrentIndex(0)
window.model_combo.blockSignals(False)
window.connection_badge.setText("●  本地模型已连接 · 演示数据")
window.retrieval.setText("知识参考：YYDS · 已确认词条")
window.show()
output = Path(__file__).resolve().parents[1] / "artifacts"
output.mkdir(exist_ok=True)
pages = [(0, "chat-preview.png"), (1, "persona-preview.png"), (2, "memory-preview.png"), (3, "learning-preview.png")]

def capture(i=0):
    index, name = pages[i]
    window.navigate(index)
    current = window.stack.currentWidget()
    if hasattr(current, "verticalScrollBar"):
        current.verticalScrollBar().setValue(0)
    def save():
        window.grab().save(str(output / name))
        if i + 1 < len(pages):
            capture(i + 1)
        else:
            window.close()
            app.quit()
    QTimer.singleShot(150, save)

QTimer.singleShot(300, capture)
app.exec()
temporary.cleanup()
