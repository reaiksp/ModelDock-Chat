from chatdock.hardware import Hardware, recommend, TEXT_MODELS


def test_gemma_is_explicitly_selectable_and_voice_budgeted():
    assert TEXT_MODELS['gemma'][0][0] == 'gemma3:12b-it-qat'
    hw = Hardware(64, 48, 24, 100, 100, 'test')
    text = recommend(hw, usage='gemma', voice=False)
    voice = recommend(hw, usage='gemma', voice=True, size='0.6B')
    assert text['text'] == voice['text'] == 'gemma3:12b-it-qat'
    assert voice['budget'] < text['budget']
    assert voice['reserve_gpu'] > 0
