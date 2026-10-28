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


def add_api(window, name="Test API", key="test-ephemeral-key"):
    window.new_provider()
    window.provider_name.setText(name)
    window.provider_base.setText("https://example.com/v1")
    window.provider_model.setText("test-model")
    window.provider_key.setText(key)
    window.provider_context.setValue(8192)
    window.save_provider()
    return window.provider_edit_id


def test_chat_can_manually_select_saved_provider(window):
    ident=add_api(window)
    window.chat_source.setCurrentIndex(window.chat_source.findData(ident))
    assert window.engine.backend=="api" and window.engine.model=="test-model"
    assert window.engine.budget.context==8192
    assert window.store.session(window.session_id)["route_id"]==ident
    assert "API 模式" in window.route_notice.text()
    window.prompt.setPlainText("hello")
    assert window.send_button.isEnabled()
    assert not window.variant_button.isEnabled()


def test_switching_provider_keeps_previous_chat_separate(window):
    old=window.session_id
    window.store.add_message(old,"user","private previous chat")
    window.store.add_message(old,"assistant","previous reply")
    ident=add_api(window)
    window.chat_source.setCurrentIndex(window.chat_source.findData(ident))
    assert window.session_id!=old
    assert window.store.messages(window.session_id)==[]
    assert len(window.store.messages(old))==2


def test_provider_keys_and_learning_routes_are_independent(window):
    a=add_api(window,"A","AAA-PRIVATE")
    b=add_api(window,"B","BBB-PRIVATE")
    assert window.provider_config(a)["key"]=="AAA-PRIVATE"
    assert window.provider_config(b)["key"]=="BBB-PRIVATE"
    assert "PRIVATE" not in str(window.store.get("providers_v2"))
    window.chat_source.setCurrentIndex(window.chat_source.findData(a))
    window.learning_mode.setCurrentIndex(window.learning_mode.findData(b))
    assert window.active_source==a and window.learning_mode.currentData()==b


def test_three_dimension_memory_table(window):
    window.store.add_memory("concise",score=9,summary="concise",analysis="adjust answer length")
    window.refresh_memory()
    assert window.memory_table.columnCount()==5
    assert window.memory_table.item(0,0).text()=="concise"
    assert window.memory_table.item(0,1).text()=="9"
    assert window.memory_table.item(0,2).text()=="adjust answer length"


def test_existing_api_session_restores_without_creating_another(window):
    ident=add_api(window)
    window.chat_source.setCurrentIndex(window.chat_source.findData(ident))
    window.store.add_message(window.session_id,"user","hello")
    count=len(window.store.sessions())
    other=Window(window.store,auto_connect=False)
    try:
        assert other.active_source==ident
        assert len(window.store.sessions())==count
        assert other.session_id==window.session_id
    finally:
        other.close()


def test_language_switch_preserves_inputs_profiles_and_reply_preference(window):
    ident=add_api(window,"保存人格","PRIVATE-KEY")
    window.chat_source.setCurrentIndex(window.chat_source.findData(ident))
    draft='保存人格\nEnglish and 中文 {literal}'
    persona='这是用户自定义提示词。Reply in my style.'
    window.prompt.setPlainText(draft)
    window.persona_prompt.setPlainText(persona)
    window.reply_language.setCurrentIndex(window.reply_language.findData("zh_CN"))
    original_profile=window.provider_config(ident).copy()
    window.i18n.set_language('en')
    assert window.nav[0].text()=='◌   Chat'
    assert window.prompt.toPlainText()==draft
    assert window.persona_prompt.toPlainText()==persona
    assert window.provider_name.text()=='保存人格'
    assert window.chat_source.currentData()==ident
    assert window.provider_config(ident)==original_profile
    assert window.reply_language.currentData()=='zh_CN'
    assert window.store.get('draft:'+str(window.session_id))==draft
    window.i18n.set_language('zh_CN')
    assert window.nav[0].text()=='◌   聊天主页'
    assert window.prompt.toPlainText()==draft


def test_memory_locale_only_changes_headers_and_status_not_user_text(window):
    ident=window.store.add_memory('保存人格',score=8,analysis='用户原文分析')
    before=window.store.items('memories')
    window.refresh_memory()
    window.i18n.set_language('en')
    assert window.memory_table.horizontalHeaderItem(0).text()=='Summary'
    assert window.memory_table.item(0,0).text()=='保存人格'
    assert window.memory_table.item(0,2).text()=='用户原文分析'
    assert window.memory_table.item(0,3).text()=='Pending review'
    assert window.store.items('memories')==before


def test_draft_restores_after_restart_and_reply_language_is_independent(window):
    window.prompt.setPlainText('我的草稿 User draft')
    window.reply_language.setCurrentIndex(window.reply_language.findData('en'))
    window.i18n.set_language('en')
    window.save_draft()
    other=Window(window.store,auto_connect=False)
    try:
        assert other.prompt.toPlainText()=='我的草稿 User draft'
        assert other.i18n.language=='en'
        assert other.reply_language.currentData()=='en'
        assert 'REPLY-LANGUAGE SETTING: ENGLISH' in other.engine.system_prompt('用户人格提示词')
        other.i18n.set_language('zh_CN')
        assert other.reply_language.currentData()=='en'
    finally:
        other.close()
