"""Five same-model compression grades and independently budgeted conversation demand."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from chatdock.hardware import (Hardware, TEXT_METADATA, TEXT_MODELS, GRADE_TEXT_MODELS,
    CONVERSATION_CONTEXTS, CONVERSATION_RESERVES, recommend)
from chatdock.store import Store
from chatdock.ui import Window


def hw(vram=64, ram=128, available=100, disk=500, model_disk=500):
    return Hardware(ram, available, vram, disk, model_disk)


@pytest.mark.parametrize("vram,effective", [(12, 1), (16, 2), (24, 3), (40, 4), (64, 5)])
def test_requested_full_precision_recommends_highest_feasible_grade(vram, effective):
    plan = recommend(hw(vram), usage="compressed", compression_level=5, preference="quality")
    assert plan["compression_level"] == 5
    assert plan["effective_compression_level"] == effective
    assert bool(plan["fallback_reason"]) == (effective < 5)
    assert plan["parameters"] == "27B"
    assert plan["memory"] <= vram - max(1, vram * .12)


@pytest.mark.parametrize("level,quant", [(1,"IQ2_XS"),(2,"IQ3_XXS"),(3,"Q4_K_M"),(4,"Q8_0"),(5,"BF16")])
def test_slider_ceiling_does_not_promote_above_requested_or_apply_speed_drop(level, quant):
    for preference in ["balanced", "speed", "quality"]:
        plan = recommend(hw(), usage="compressed", compression_level=level, preference=preference)
        assert plan["effective_compression_level"] == level and plan["quantization"] == quant
        assert not plan["fallback_reason"]


def test_legacy_none_uses_only_original_four_gsq_models():
    assert len(TEXT_MODELS["compressed"]) == 4
    plan = recommend(hw(), usage="compressed", preference="quality")
    assert plan["quantization"] == "IQ3_S"
    assert plan["compression_level"] is None and plan["effective_compression_level"] == 0
    assert set(GRADE_TEXT_MODELS) == {1,2,3,4,5}
    for grade, row in GRADE_TEXT_MODELS.items():
        metadata = TEXT_METADATA[row[0]]
        assert metadata["compression_grade"] == grade
        assert metadata["parameters"] == "27B"
        assert "-mtp" not in metadata["filename"].lower()
        assert row[1] * 1024**3 >= metadata["size_bytes"]
    assert TEXT_METADATA[GRADE_TEXT_MODELS[5][0]]["quantization"] == "BF16"
    assert GRADE_TEXT_MODELS[5][0].startswith("hf.co/ggml-org/")


def test_graded_auto_voice_prioritizes_text_grade_before_voice_size():
    # At 28 GiB, grade 3 fits with 0.6B TTS but not with 1.7B TTS.
    graded = recommend(hw(28), voice=True, size="auto", usage="compressed", preference="quality", compression_level=5)
    assert graded["effective_compression_level"] == 3 and graded["voice"] == "0.6B"
    legacy = recommend(hw(28), voice=True, size="auto", usage="compressed", preference="quality")
    assert legacy["voice"] == "1.7B" and legacy["quantization"] == "IQ3_S"
    # Fixed audio choices are never silently changed.
    fixed = recommend(hw(28), voice=True, size="1.7B", usage="compressed", preference="quality", compression_level=5)
    assert fixed["voice"] == "1.7B" and fixed["effective_compression_level"] == 2


@pytest.mark.parametrize("preference,voice", [("quality","1.7B"),("balanced","0.6B"),("speed","0.6B")])
def test_same_text_grade_breaks_auto_voice_ties_by_preference(preference, voice):
    plan = recommend(hw(), voice=True, size="auto", usage="compressed", preference=preference, compression_level=1)
    assert plan["effective_compression_level"] == 1 and plan["voice"] == voice


@pytest.mark.parametrize("demand", range(1,6))
def test_conversation_demand_has_exact_context_and_reserve(demand):
    plan = recommend(hw(24), usage="compressed", compression_level=1, conversation_demand=demand)
    assert plan["context"] == CONVERSATION_CONTEXTS[demand]
    assert plan["context_reserve"] == CONVERSATION_RESERVES[demand]
    assert plan["conversation_demand"] == demand
    assert plan["memory"] + plan["context_reserve"] <= plan["budget"] + .051


def test_context_demand_never_automatically_shrinks_to_fit():
    assert recommend(hw(12), usage="compressed", compression_level=5, conversation_demand=5) is None
    plan = recommend(hw(24), usage="compressed", compression_level=5, conversation_demand=5)
    assert plan["context"] == 32768 and plan["effective_compression_level"] == 2
    assert recommend(hw(16), voice=True, size="0.6B", usage="compressed", compression_level=5, conversation_demand=4) is None


def test_full_precision_needs_disk_for_cache_and_model():
    plan = recommend(hw(model_disk=80), usage="compressed", compression_level=5, preference="quality")
    assert plan["effective_compression_level"] == 4
    assert plan["fallback_reason"]
    assert recommend(hw(vram=0, ram=8, available=5), usage="compressed", compression_level=5, preference="quality") is None


def test_cpu_budget_and_noncompressed_choices_remain_honest():
    assert recommend(hw(vram=0), usage="compressed", compression_level=5, preference="balanced") is None
    plan = recommend(hw(vram=0), usage="compressed", compression_level=5, preference="quality")
    assert plan["mode"] == "CPU" and plan["quantization"] == "BF16"
    ordinary = recommend(hw(16), usage="chat", compression_level=5, conversation_demand=5)
    assert ordinary["compression_level"] is None and ordinary["effective_compression_level"] == 0
    assert ordinary["context"] == 32768


def test_remote_speech_plan_does_not_claim_provider_context_or_full_precision():
    plan = recommend(hw(10, ram=16, available=7, model_disk=0), voice=True, size="1.7B",
        usage="compressed", remote_text=True, compression_level=5, conversation_demand=5)
    assert plan["text"] == "API" and plan["memory"] == 0 and plan["context_reserve"] == 0
    assert plan["context"] is None and plan["conversation_demand"] is None
    assert plan["compression_level"] is None and plan["effective_compression_level"] == 0


@pytest.mark.parametrize("name,value", [("compression_level",0),("compression_level",True),("conversation_demand",6),("conversation_demand","5")])
def test_invalid_slider_backend_values_are_rejected(name, value):
    with pytest.raises(ValueError): recommend(hw(), **{name:value})


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    win=Window(Store(tmp_path/"chat.db"),auto_connect=False)
    yield win
    win.job=None
    win.close()


def test_two_sliders_persist_without_download_or_current_model_mutation(window, monkeypatch):
    panel=window.speech
    window.engine.model="current-model";window.engine.context=4096
    window.prompt.setPlainText("原始草稿")
    window.store.set("model_demands",{"current-model":2})
    monkeypatch.setattr(window,"run_job",lambda *a,**kw:pytest.fail("slider must not launch a task"))
    panel.usage.setCurrentIndex(panel.usage.findData("compressed"))
    panel.compression_slider.setValue(5);panel.conversation_slider.setValue(5)
    saved=window.store.get("speech")
    assert saved["compression_level"]==5 and saved["conversation_demand"]==5
    assert window.store.get("model_demands")=={"current-model":2}
    assert window.engine.model=="current-model" and window.engine.context==4096
    assert window.prompt.toPlainText()=="原始草稿"
    assert panel.compression_slider.minimum()==panel.conversation_slider.minimum()==1
    assert panel.compression_slider.maximum()==panel.conversation_slider.maximum()==5
    assert panel.compression_slider.tickInterval()==panel.conversation_slider.tickInterval()==1


def test_sliders_and_busy_state_do_not_reenable_during_refresh(window):
    panel=window.speech
    assert not panel.compression_slider.isEnabled() and panel.conversation_slider.isEnabled()
    panel.usage.setCurrentIndex(panel.usage.findData("compressed"));panel.scanned(hw())
    assert panel.compression_slider.isEnabled()
    panel.busy(True);panel.update_recommendations()
    assert not panel.compression_slider.isEnabled() and not panel.conversation_slider.isEnabled()
    assert not panel.text_deploy.isEnabled()
    panel.busy(False)
    panel.remote_text.setChecked(True)
    assert not panel.compression_slider.isEnabled() and not panel.conversation_slider.isEnabled()
    panel.update_recommendations()
    assert "API" in panel.describe(panel.plan) and panel.plan["context"] is None
    panel.remote_text.setChecked(False)
    assert panel.compression_slider.isEnabled() and panel.conversation_slider.isEnabled()


def test_legacy_capacity_migrates_and_new_settings_restore(app, tmp_path):
    store=Store(tmp_path/"old.db");store.set("speech",{"capacity":"context","speaker":"Serena","instruct":"保留语气","custom_future":{"keep":True}})
    win=Window(store,auto_connect=False)
    try:
        assert win.speech.conversation_slider.value()==4
        assert win.speech.compression_slider.value()==3
        assert win.speech.style.text()=="保留语气"
        win.speech.conversation_slider.setValue(3)
    finally:win.close()
    restored=Window(store,auto_connect=False)
    try:
        assert restored.speech.conversation_slider.value()==3
        assert restored.speech.style.text()=="保留语气"
        assert restored.speech.snapshot()["custom_future"]=={"keep":True}
    finally:restored.close()


def test_only_successful_explicit_deployment_records_model_demand(window, monkeypatch):
    panel=window.speech
    panel.usage.setCurrentIndex(panel.usage.findData("compressed"))
    panel.compression_slider.setValue(5);panel.conversation_slider.setValue(4);panel.scanned(hw())
    window.store.set("model_demands",{"other":1})
    callbacks=[]
    monkeypatch.setattr(window,"run_job",lambda fn,done,kind:callbacks.append((done,kind)))
    panel.deploy_text()
    assert window.store.get("model_demands")=={"other":1}
    assert callbacks[0][1]=="text_deploy"
    callbacks[0][0]("normalized-installed-model")
    assert window.store.get("model_demands")=={"other":1,"normalized-installed-model":4}
    assert window.store.get("model_contexts")["normalized-installed-model"]==16384
