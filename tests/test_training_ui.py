import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from chatdock.store import Store
from chatdock.ui import Window


def test_training_editor_keeps_transcripts_draft_and_provider_across_languages(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path/'chat.db')
    window = Window(store, auto_connect=False)
    panel = window.speech.training_panel
    raw = '请保留我的 User Input 和偏好'
    window.prompt.setPlainText(raw)
    window.engine.use_provider({'name': 'My API', 'base': 'https://example.com/v1',
                                'model': 'my-model', 'context': 8192, 'key': ''})
    provider = (window.engine.backend, window.engine.provider_name, window.engine.model)
    wav = tmp_path/'一.wav'; wav.write_bytes(b'original')
    panel.set_rows([{'audio': str(wav), 'text': '不是界面标签，是我的准确转写。'}])
    window.i18n.set_language('en')
    window.i18n.apply()
    assert panel.rows()[0]['text'] == '不是界面标签，是我的准确转写。'
    assert window.prompt.toPlainText() == raw
    assert (window.engine.backend, window.engine.provider_name, window.engine.model) == provider
    assert 'Multi-audio training' in [window.speech.tabs.tabText(i) for i in range(window.speech.tabs.count())]
    assert panel.manager is None
    assert not (tmp_path/'speech/training').exists()
    window.close()
    app.processEvents()


def test_tone_controls_are_capability_gated_and_do_not_clear_saved_prompt(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(Store(tmp_path/'chat.db'), auto_connect=False)
    speech = window.speech
    speech.style.setText('我自己的语气偏好')
    speech.variant.setCurrentIndex(speech.variant.findData('0.6B'))
    assert not speech.style.isEnabled() and not speech.tone_preset.isEnabled()
    assert speech.style.text() == '我自己的语气偏好'
    speech.variant.setCurrentIndex(speech.variant.findData('1.7B'))
    assert speech.style.isEnabled() and speech.tone_preset.isEnabled()
    assert speech.style.text() == '我自己的语气偏好'
    window.close()
    app.processEvents()
