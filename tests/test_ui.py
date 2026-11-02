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


def test_window_has_six_independent_pages(window):
    assert window.stack.count() == 6
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


def test_voice_language_switch_and_restart_preserve_user_preferences(window, app):
    from chatdock.ui import Window
    panel = window.speech
    panel.mode.setCurrentIndex(1)
    panel.variant.setCurrentIndex(panel.variant.findData("1.7B"))
    panel.style.setText("温柔地说，保留我的语气")
    panel.dictionary.setPlainText("yyds=永远的神")
    panel.language.setCurrentIndex(panel.language.findData("Chinese"))
    assert panel.save()
    from chatdock.hardware import Hardware
    panel.scanned(Hardware(32,25,16,100,100))
    window.i18n.set_language("en")
    window.i18n.apply()
    assert "Text:" in panel.with_voice.text()
    assert panel.usage.currentText() == "General chat"
    assert "Voice" in window.nav[5].text()
    assert panel.mode.currentData() is True
    assert panel.variant.currentData() == "1.7B"
    assert panel.style.text() == "温柔地说，保留我的语气"
    assert panel.dictionary.toPlainText() == "yyds=永远的神"
    window.close()
    reopened = Window(window.store, auto_connect=False)
    assert reopened.speech.style.text() == "温柔地说，保留我的语气"
    assert reopened.speech.language.currentData() == "Chinese"
    reopened.close()


def test_failed_chat_does_not_auto_read_previous_reply(window):
    window.store.add_message(window.session_id, "assistant", "上一轮的旧回复")
    window.speech.mode.setCurrentIndex(1)
    window.speech.auto.setChecked(True)
    window.job_kind = "chat"
    window.chat_succeeded = False
    window.job_finished()
    assert window.speech.pending_auto is None


def test_switching_conversation_clears_queued_voice(window):
    window.speech.pending_auto = (window.session_id, 123)
    window.speech.queue = ["not-played.wav"]
    window.new_session()
    assert window.speech.pending_auto is None and window.speech.queue == []


def test_import_md_plan_keeps_user_content_and_clamps_later(window,tmp_path,monkeypatch):
    import json
    root=tmp_path/"ModelDock"
    root.mkdir()
    (root/"settings.json").write_text(json.dumps({"model":"qwen3:1.7b","model_plans":{"qwen3:1.7b":{"model":"qwen3:1.7b","context":16384,"capacity":"context","voice":"1.7B"}}}),encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA",str(tmp_path))
    monkeypatch.setattr(window,"connect",lambda:None)
    window.prompt.setPlainText("我的草稿保持原样")
    before=window.store.persona(1)["prompt"]
    window.import_md_plan()
    assert window.store.get("model_contexts")["qwen3:1.7b"]==16384
    assert window.speech.capacity.currentData()=="context"
    assert window.speech.variant.currentData()=="1.7B"
    assert window.prompt.toPlainText()=="我的草稿保持原样"
    assert window.store.persona(1)["prompt"]==before


def test_md_import_stops_owned_speech_before_connecting(window,tmp_path,monkeypatch):
    import json
    root=tmp_path/"ModelDock"
    root.mkdir()
    (root/"settings.json").write_text(json.dumps({"model":"qwen3:8b","model_plans":{"qwen3:8b":{"model":"qwen3:8b","context":16384,"capacity":"context","voice":"off"}}}),encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA",str(tmp_path))
    window.speech.mode.setCurrentIndex(1)
    window.speech.service.owned=True
    calls=[]
    monkeypatch.setattr(window,"connect",lambda:calls.append("connect"))
    monkeypatch.setattr(window.speech.service,"stop",lambda:calls.append("stop"))
    def run(fn,done,kind):
        assert kind=="speech_stop"
        fn(None)
        done(None)
    monkeypatch.setattr(window,"run_job",run)
    window.import_md_plan()
    assert calls==["stop"]
    window.after_job()
    assert calls==["stop","connect"]
    window.speech.service.owned=False

def test_personal_model_controls_follow_api_backend(window):
    ident = add_api(window)
    window.chat_source.setCurrentIndex(window.chat_source.findData(ident))
    assert not window.personal_save.isEnabled()
    assert not window.personal_preview.isEnabled()
    assert not window.personal_delete.isEnabled()
    assert window.engine.backend == 'api'


def test_personal_model_language_toggle_preserves_draft_and_options(window):
    raw = '原始输入和人格必须保持🙂'
    window.prompt.setPlainText(raw)
    window.personal_persona.setChecked(False)
    window.personal_auto.setChecked(True)
    window.i18n.set_language('en')
    assert window.personal_preview.text() == 'Preview snapshot contents'
    assert 'automatic' in window.personal_auto.text().lower()
    assert 'No private' in window.personal_info.text()
    assert window.prompt.toPlainText() == raw
    assert window.store.get('personal_model_persona') is False
    window.i18n.set_language('zh_CN')
    assert '尚未' in window.personal_info.text()
    assert window.prompt.toPlainText() == raw


def test_preview_translates_counts_without_rewriting_personal_text(window, monkeypatch):
    window.engine.model = 'test'
    window.store.set('user_profile', '我希望保留这段中文原文。')
    window.i18n.set_language('en')
    shown = []
    monkeypatch.setattr(window, 'show_memory_text', lambda title, text: shown.append(text))
    window.preview_personal_model()
    assert shown and 'Included' in shown[0] and 'Budget' in shown[0]
    assert '我希望保留这段中文原文。' in shown[0]


def test_save_enables_auto_refresh_only_after_success(window, monkeypatch):
    window.connected = True
    window.engine.model = 'test'
    window.prompt.setPlainText('保留草稿')
    monkeypatch.setattr(window.engine, 'sync_personal_model', lambda *a, **kw: {'name': 'mdc-private-test'})
    class Job:
        task = object()
        class event:
            @staticmethod
            def emit(*a):
                pass
    def run(fn, done, kind):
        assert kind == 'personal_model'
        assert window.store.get('personal_model_auto', False) is False
        done(fn(Job()))
    monkeypatch.setattr(window, 'run_job', run)
    window.save_personal_model()
    assert window.store.get('personal_model_auto') is True
    assert window.prompt.toPlainText() == '保留草稿'


def test_unverified_private_versions_are_marked_and_not_reported_as_ready(window):
    from chatdock.personal_model import build_snapshot
    snapshot = build_snapshot(window.store, 1, 4096)
    window.store.save_personal_model('mdc-private-bad', 'test', 1, window.session_id, 4096, snapshot, verified=False)
    window.refresh_personal_models()
    assert window.personal_picker.currentText().endswith('!')
    assert '尚无通过校验' in window.personal_info.text()
    window.i18n.set_language('en')
    assert 'No verified' in window.personal_info.text()
    assert 'unverified' in window.personal_info.text()


def test_import_md_dual_sliders_and_memory_policy_keeps_api_draft(window, tmp_path, monkeypatch):
    import json
    from chatdock.hardware import GRADE_TEXT_MODELS
    tag = GRADE_TEXT_MODELS[2][0]
    root = tmp_path / "ModelDock"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"model": tag, "model_plans": {tag: {
        "model": tag, "context": 8192, "capacity": "large", "voice": "0.6B",
        "usage": "压缩大模型", "compression_level": 5, "effective_compression_level": 2,
        "conversation_demand": 3}}}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(window, "connect", lambda: None)
    window.engine.backend = "api"
    window.engine.model = "active-provider-model"
    window.prompt.setPlainText("原始草稿🙂")
    window.import_md_plan()
    assert window.speech.compression_slider.value() == 5
    assert window.speech.conversation_slider.value() == 4
    assert window.speech.usage.currentData() == "compressed"
    assert window.store.get("model_contexts")[tag] == 8192
    assert window.store.get("model_demands")[tag] == 4
    assert window.engine.model == "active-provider-model" and window.engine.backend == "api"
    assert window.prompt.toPlainText() == "原始草稿🙂"


def test_parameter_slider_covers_intermediate_positions_and_preserves_chat(window):
    from chatdock.hardware import Hardware
    window.speech.scanned(Hardware(256,230,240,1000,1000))
    window.engine.model = 'active-api-model'
    window.prompt.setPlainText('原文不改🙂')
    slider = window.speech.parameter_slider
    assert (slider.minimum(), slider.maximum()) == (1, 96)
    for step in range(1, 97):
        slider.setValue(step)
        assert window.speech.plan['actual_parameters'] <= step / 2
        assert window.store.get('speech')['parameter_limit'] == step / 2
        assert window.engine.model == 'active-api-model' and not window.job
    assert window.prompt.toPlainText() == '原文不改🙂'
    window.speech.remote_text.setChecked(True)
    assert not slider.isEnabled() and slider.value() == 96


def test_import_md_parameter_limit_and_actual_quantized_model(window, tmp_path, monkeypatch):
    import json
    from chatdock.parameters import VARIANTS
    row = next(r for r in VARIANTS if r['parameters'] == 3 and r['grade'] == 3)
    tag = row['tag']
    directory = tmp_path / 'ModelDock'
    directory.mkdir()
    (directory / 'settings.json').write_text(json.dumps({'model':tag, 'model_plans':{tag:{'model':tag,'context':8192,'parameter_limit':3.5,'actual_parameters':3,'compression_level':3,'conversation_demand':3,'usage':'压缩大模型'}}}),encoding='utf-8')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(window, 'connect', lambda:None)
    window.import_md_plan()
    assert window.speech.parameter_slider.value() == 7
    assert window.speech.usage.currentData() == 'compressed'
    assert window.store.get('model') == tag
    assert window.store.get('model_contexts')[tag] == 8192


@pytest.mark.parametrize('context', [2048,6144,10240])
def test_import_new_2k_step_plan_and_metadata_preserve_exact_context(window,tmp_path,monkeypatch,context):
    import json
    directory=tmp_path/'ModelDock';directory.mkdir()
    tag='test-model'
    plan={'model':tag,'context':context,'conversation_demand':context//2048,'conversation_scale':2,'voice':'off'}
    (directory/'settings.json').write_text(json.dumps({'model':tag,'model_plans':{tag:plan}}),encoding='utf-8')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    monkeypatch.setattr(window,'connect',lambda:None)
    window.prompt.setPlainText('保留我的草稿🙂')
    window.import_md_plan()
    assert window.speech.conversation_slider.value()==context//2048
    assert window.store.get('model_contexts')[tag]==context
    window.engine.model=tag
    window.engine.backend='local'
    monkeypatch.setattr(window,'run_job',lambda fn,done,kind:done(32768))
    window.read_model_info()
    assert window.engine.context==context
    assert window.context_combo.currentData()==context
    assert window.prompt.toPlainText()=='保留我的草稿🙂'


def test_storage_controls_route_to_shared_backend_and_keep_api_draft(window,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from chatdock import storage
    window.engine.backend='api';window.engine.model='keep-provider-model'
    window.prompt.setPlainText('保留草稿和提示词🙂')
    panel=window.speech;target=str(tmp_path/'new-disk')
    panel.text_storage.setText(target)
    called=[]
    monkeypatch.setattr(storage,'apply_text_directory',lambda value,*args:called.append(value) or {'target':value})
    callbacks=[]
    monkeypatch.setattr(window,'run_job',lambda fn,done,kind:callbacks.append((fn,done,kind)))
    panel.apply_storage('text')
    fn,done,kind=callbacks[0]
    assert kind=='model_storage'
    done(fn(SimpleNamespace(task=object(),event=SimpleNamespace(emit=lambda *args:None))))
    assert called==[target] and window.after_job==panel.scan
    assert window.engine.model=='keep-provider-model' and window.engine.backend=='api'
    assert window.prompt.toPlainText()=='保留草稿和提示词🙂'
    window.i18n.set_language('en')
    assert 'model folder' in panel.info.text().lower()
