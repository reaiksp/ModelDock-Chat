"""Same-parameter GSQ/RCO model choices must fit actual joint budgets."""
import math
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from chatdock.hardware import Hardware, TEXT_METADATA, TEXT_MODELS, recommend
from chatdock.store import Store
from chatdock.ui import Window


def hardware(vram=16, ram=32, available=25, disk=100, model_disk=100):
    return Hardware(ram, available, vram, disk, model_disk)


@pytest.mark.parametrize('vram,quant', [(12, 'IQ2_XS'), (16, 'IQ3_S'), (24, 'IQ3_S')])
def test_compressed_choice_for_gpu_budget(vram, quant):
    plan = recommend(hardware(vram), usage='compressed', preference='quality')
    assert plan and plan['quantization'] == quant
    assert plan['parameters'] == '27B'
    assert plan['base_model'] == 'Qwen/Qwen3.8-27B'
    assert plan['memory'] <= vram - max(1, vram * .12)


def test_catalog_uses_exact_non_mtp_files_and_preserves_existing_uses():
    assert [m[0] for m in TEXT_MODELS['chat']] == ['qwen3:0.6b', 'qwen3:1.7b', 'qwen3:4b', 'qwen3:8b', 'qwen3:14b', 'qwen3:32b']
    assert len(TEXT_MODELS['compressed']) == 4
    for tag, download, memory in TEXT_MODELS['compressed']:
        data = TEXT_METADATA[tag]
        assert tag == 'hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:Qwen3.8-27B-GSQ-RCO-' + data['quantization'] + '.gguf'
        assert '-mtp' not in tag and 'mmproj' not in tag
        assert download == math.ceil(data['size_bytes'] / 1024**3 * 10) / 10
        assert download * 1024**3 >= data['size_bytes']
        assert len(data['sha256']) == 64
        assert data['method'] == 'GSQ + RCO'
        assert data['source'].startswith('https://huggingface.co/ISTA-DASLab/')
        assert memory >= download


def test_compressed_16k_keeps_extra_context_reserve():
    assert recommend(hardware(12), usage='compressed', capacity='context') is None
    plan = recommend(hardware(16), usage='compressed', capacity='context')
    assert plan['quantization'] == 'IQ2_XS'
    assert plan['context'] == 16384 and plan['context_reserve'] == 4
    assert plan['memory'] + 4 <= 16 - 16 * .12


def test_voice_reserve_can_reject_all_compressed_models_without_tiny_fallback():
    assert recommend(hardware(16), voice=True, size='1.7B', usage='compressed') is None
    small_voice = recommend(hardware(16), voice=True, size='0.6B', usage='compressed')
    assert small_voice['quantization'] == 'IQ2_XS'
    assert small_voice['reserve_gpu'] == 4 and small_voice['reserve_ram'] == 4
    assert small_voice['memory'] + 4 <= 16 - 16 * .12
    large_voice = recommend(hardware(24), voice=True, size='1.7B', usage='compressed')
    assert large_voice['quantization'] == 'IQ3_S'
    assert large_voice['memory'] + 7 <= 24 - 24 * .12


def test_long_context_and_voice_cannot_spend_same_vram_twice():
    plan = recommend(hardware(24), voice=True, size='1.7B', usage='compressed', capacity='context')
    assert plan and plan['quantization'] == 'IQ2_XS'
    assert plan['memory'] + plan['context_reserve'] + plan['reserve_gpu'] <= 24 - 24 * .12
    assert recommend(hardware(16), voice=True, size='0.6B', usage='compressed', capacity='context') is None


def test_cpu_large_models_require_quality_preference():
    assert recommend(hardware(0), usage='compressed', preference='balanced') is None
    assert recommend(hardware(0), usage='compressed', preference='speed') is None
    plan = recommend(hardware(0), usage='compressed', preference='quality')
    assert plan and plan['mode'] == 'CPU' and plan['parameters'] == '27B'


def test_low_ram_or_disk_does_not_recommend_smaller_parameter_substitute():
    assert recommend(hardware(24, ram=4, available=1.5), usage='compressed') is None
    assert recommend(hardware(0, ram=8, available=4), usage='compressed', preference='quality') is None
    assert recommend(hardware(24, model_disk=9), usage='compressed') is None
    assert recommend(hardware(0, ram=32, available=25), usage='compressed', voice=True, preference='quality') is None


def test_remote_text_ignores_compressed_local_text_sizes():
    plan = recommend(hardware(10, ram=16, available=6.5, model_disk=0), voice=True, size='1.7B', usage='compressed', remote_text=True)
    assert plan and plan['text'] == 'API' and plan['memory'] == 0
    assert 'quantization' not in plan


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_compressed_ui_is_opt_in_and_shows_source(app, tmp_path):
    win = Window(Store(tmp_path / 'chat.db'), auto_connect=False)
    try:
        assert win.speech.usage.currentData() == 'chat'
        assert win.speech.compression_note.isHidden()
        win.speech.usage.setCurrentIndex(win.speech.usage.findData('compressed'))
        assert not win.speech.compression_note.isHidden()
        assert '27B' in win.speech.compression_note.text()
        assert 'huggingface.co/ISTA-DASLab/' in win.speech.compression_source.text()
        win.speech.scanned(hardware())
        assert win.speech.plan['parameters'] == '27B'
        assert 'GSQ + RCO' in win.speech.describe(win.speech.plan)
        assert win.store.get('speech')['usage'] == 'compressed'
    finally:
        win.close()


def test_deployment_selection_does_not_switch_api_or_rewrite_user_data(app, tmp_path):
    store = Store(tmp_path / 'chat.db')
    store.set('user_profile', '我的原文偏好')
    store.set('model_contexts', {'API model': 32768})
    win = Window(store, auto_connect=False)
    try:
        win.engine.backend = 'api'
        win.engine.model = 'API model'
        win.engine.context = win.engine.model_limit = 32768
        win.prompt.setPlainText('原始草稿')
        win.speech.usage.setCurrentIndex(win.speech.usage.findData('compressed'))
        win.speech.remote_text.setChecked(True)
        win.speech.scanned(hardware())
        assert win.engine.backend == 'api' and win.engine.model == 'API model'
        assert win.engine.context == 32768
        assert store.get('model_contexts') == {'API model': 32768}
        assert win.prompt.toPlainText() == '原始草稿'
        assert store.get('user_profile') == '我的原文偏好'
        assert win.speech.plan['text'] == 'API'
    finally:
        win.close()
