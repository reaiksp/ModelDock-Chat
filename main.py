import json
import logging
from logging.handlers import RotatingFileHandler
import traceback
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from chatdock.store import Store, data_dir
from chatdock.ui import STYLE, Window
from chatdock.i18n import tr


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ModelDockChat")
    app.setOrganizationName("ModelDockChat")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    smoke = "--smoke-test" in sys.argv
    temporary = tempfile.TemporaryDirectory(prefix="modeldock-chat-smoke-") if smoke else None
    log_root = Path(temporary.name) if temporary else data_dir() / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_root / "application.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    def exception_hook(kind, value, tb):
        logging.error("Unhandled application error", exc_info=(kind, value, tb))
        if smoke:
            traceback.print_exception(kind, value, tb)
            app.exit(1)
            return
        QMessageBox.critical(None, "ModelDock Chat", tr("发生错误：{0}").format(value) + "\n" + str(log_root / "application.log"))
    sys.excepthook = exception_hook
    window = Window(Store(Path(temporary.name) / "chat.db") if smoke else None, auto_connect=not smoke)
    if smoke:
        import wave
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        audio_path = Path(temporary.name) / "smoke.wav"
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 2400)
        window.speech.audio.setVolume(0)
        window.speech.player.setSource(QUrl.fromLocalFile(str(audio_path)))
        path = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        def check():
            window.i18n.set_language("en")
            window.i18n.apply()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"startup": "passed", "frozen": bool(getattr(sys, "frozen", False)),
                "pages": window.stack.count(), "speech_bridge_bundled": Path(__file__).parent.joinpath("chatdock/tts_server.py").exists(),
                "audio_backend_loaded": window.speech.player.mediaStatus() in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia},
                "english_ui": "Chat" in window.nav[0].text(), "independent_database": True,
                "input_limit": window.engine.budget.input_limit}, indent=2), "utf-8")
            window.close()
            app.quit()
        QTimer.singleShot(1000, check)
    else:
        window.show()
    code = app.exec()
    # Flush deferred multimedia destruction before deleting temporary WAV files on Windows.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    handler.close()
    logging.getLogger().removeHandler(handler)
    if temporary:
        temporary.cleanup()
    sys.exit(code)


if __name__ == "__main__":
    main()
