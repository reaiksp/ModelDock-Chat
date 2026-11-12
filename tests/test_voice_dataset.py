import json
from pathlib import Path

import pytest

from chatdock.voice_dataset import audio_rows, apply_transcripts, validate_rows, import_batch
from chatdock.voice_training import VoiceTrainingManager


def test_pair_exact_sidecars_and_keep_original_text(tmp_path):
    wav = tmp_path / '你好.wav'
    wav.write_bytes(b'audio')
    wav.with_suffix('.txt').write_text('\ufeff这就是我说的内容。', 'utf-8')
    rows = audio_rows([wav, wav])
    assert len(rows) == 1 and rows[0]['text'] == '这就是我说的内容。'
    csv = tmp_path / 'transcripts.csv'
    csv.write_text('audio,text\n你好.wav,"更正，逐字内容"\n', 'utf-8-sig')
    updated = apply_transcripts(rows, csv)
    assert updated[0]['text'] == '更正，逐字内容'
    assert rows[0]['text'] == '这就是我说的内容。'
    assert validate_rows(updated)


def test_csv_rejects_ambiguous_and_unselected_names(tmp_path):
    a, b = tmp_path / 'a.wav', tmp_path / 'other/a.wav'
    b.parent.mkdir()
    for p in [a, b]: p.write_bytes(b'x')
    csv = tmp_path / 't.csv'
    csv.write_text('audio,text\na.wav,hello\n', 'utf-8')
    with pytest.raises(ValueError, match='同名'): apply_transcripts(audio_rows([a,b]), csv)
    csv.write_text('audio,text\nmissing.wav,hello\n', 'utf-8')
    with pytest.raises(ValueError, match='未选择'): apply_transcripts(audio_rows([a]), csv)
    csv.write_text('audio,text\na.wav,one\nA.wav,two\n', 'utf-8')
    with pytest.raises(ValueError, match='重复'): apply_transcripts(audio_rows([a]), csv)


def manager(tmp_path, fail_at=None):
    count = 0
    def normalize(src, out, seconds):
        nonlocal count
        count += 1
        if count == fail_at: raise ValueError('bad audio')
        Path(out).write_bytes(b'RIFF' + b'x'*100)
        return {'duration': 12, 'sampleRate': 24000}
    return VoiceTrainingManager(root=tmp_path/'projects', normalizer=normalize)


def test_multiple_recordings_use_one_reference_and_generate_jsonl(tmp_path):
    paths = [tmp_path/f'{i}.wav' for i in range(3)]
    for p in paths: p.write_bytes(b'original')
    rows = [dict(audio=str(p), text=f'内容 {i}') for i,p in enumerate(paths)]
    m = manager(tmp_path)
    result = import_batch(m, rows, 1)
    records = [json.loads(line) for line in Path(result['path']).read_text('utf-8').splitlines()]
    assert len(records) == 3 and len({r['ref_audio'] for r in records}) == 1
    assert [r['text'] for r in records] == ['内容 0','内容 1','内容 2']
    assert all(p.read_bytes() == b'original' for p in paths)


def test_failed_batch_preserves_previous_project_and_reference(tmp_path):
    wav = tmp_path/'first.wav'
    wav.write_bytes(b'input')
    m = manager(tmp_path, fail_at=5)
    import_batch(m,[{'audio':str(wav),'text':'原来的'}])
    before = m.project_file().read_bytes()
    reference = (m.project_dir()/'reference.wav').read_bytes()
    raw = (m.project_dir()/'train_raw.jsonl').read_bytes()
    files = set((m.project_dir()/'audio').iterdir())
    two = tmp_path/'second.wav'; two.write_bytes(b'input2')
    with pytest.raises(Exception, match='bad audio'):
        import_batch(m,[{'audio':str(wav),'text':'一'},{'audio':str(two),'text':'二'}])
    assert [r['text'] for r in m.clips()] == ['原来的']
    assert (m.project_dir()/'reference.wav').read_bytes() == reference
    assert (m.project_dir()/'train_raw.jsonl').read_bytes() == raw
    assert set((m.project_dir()/'audio').iterdir()) == files


def test_invalid_text_rejected_before_any_conversion(tmp_path):
    p = tmp_path/'a.wav'; p.write_bytes(b'input')
    m = manager(tmp_path)
    with pytest.raises(ValueError): import_batch(m,[{'audio':str(p),'text':''}])
    assert m.clips() == []
