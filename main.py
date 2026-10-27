import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from chatdock.store import Store
from chatdock.ui import STYLE, Window


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ModelDockChat")
    app.setOrganizationName("ModelDockChat")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    def exception_hook(kind, value, tb):
        QMessageBox.critical(None, "ModelDock Chat", f"发生错误：{value}")
    sys.excepthook = exception_hook
    smoke = "--smoke-test" in sys.argv
    temporary = tempfile.TemporaryDirectory(prefix="modeldock-chat-smoke-") if smoke else None
    window = Window(Store(Path(temporary.name) / "chat.db") if smoke else None, auto_connect=not smoke)
    if smoke:
        path = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        def check():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"startup": "passed", "frozen": bool(getattr(sys, "frozen", False)),
                "pages": window.stack.count(), "independent_database": True,
                "input_limit": window.engine.budget.input_limit}, indent=2), "utf-8")
            window.close()
            app.quit()
        QTimer.singleShot(500, check)
    else:
        window.show()
    code = app.exec()
    if temporary:
        temporary.cleanup()
    sys.exit(code)


if __name__ == "__main__":
    main()
