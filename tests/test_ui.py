import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from chatdock.store import Store
from chatdock.ui import STYLE, Window


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture
def window(app, tmp_path):
    win = Window(Store(tmp_path / "chat.db"), auto_connect=False)
    yield win
    win.close()


def test_window_has_five_independent_pages(window):
    assert window.stack.count() == 5
    assert window.learning_tabs.count() == 3
    assert "保守估算" in window.counter.text()


def test_oversized_prompt_warns_and_disables_send(window):
    window.connected = True
    window.prompt.setPlainText("中文" * 5000)
    assert "超限" in window.counter.text()
    assert not window.send_button.isEnabled()
    assert window.prompt.toPlainText() == "中文" * 5000


def test_persona_budget_blocks_save(window):
    window.persona_prompt.setPlainText("性格" * 5000)
    assert "超限" in window.persona_limit.text()
    assert not window.persona_save.isEnabled()


def test_new_persona_chat_uses_selected_persona(window):
    ident = window.store.save_persona(None, "Test", "Be concise.")
    window.refresh_personas()
    window.new_session(persona_id=ident)
    assert window.store.session(window.session_id)["persona_id"] == ident
    assert window.persona_combo.currentData() == ident
