"""Local batch pairing; never guesses transcripts from filenames or sends audio away."""
import csv
from pathlib import Path


def audio_rows(paths):
    rows, seen = [], set()
    for value in paths:
        path = Path(value).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            raise ValueError(f"音频不存在：{path.name}")
        sidecar = path.with_suffix('.txt')
        text = sidecar.read_text('utf-8-sig').strip() if sidecar.is_file() else ''
        rows.append({'audio': str(path), 'text': text})
    return rows


def apply_transcripts(rows, csv_path):
    """Match exact filenames (including extension); reject ambiguity before editing."""
    mapping = {}
    with Path(csv_path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'audio', 'text'} <= set(reader.fieldnames or []):
            raise ValueError('CSV 需要 audio,text 两列表头。')
        for item in reader:
            name, text = (item.get('audio') or '').strip(), (item.get('text') or '').strip()
            if not name or not text or Path(name).name != name:
                raise ValueError('CSV 每行需要音频文件名（含扩展名）及准确文本。')
            if name.casefold() in mapping:
                raise ValueError(f'CSV 文件名重复：{name}')
            mapping[name.casefold()] = text
    names = [Path(row['audio']).name.casefold() for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('不同目录存在同名音频，请先重命名以免配错文本。')
    if set(mapping) - set(names):
        raise ValueError('CSV 中有未选择的音频；请先批量选择对应文件。')
    return [dict(row, text=mapping.get(name, row['text'])) for row, name in zip(rows, names)]


def validate_rows(rows):
    if not rows:
        raise ValueError('请先添加音频。')
    seen = set()
    for number, row in enumerate(rows, 1):
        path = Path(row['audio']).resolve()
        if not path.is_file():
            raise ValueError(f'第 {number} 行音频不存在。')
        if path in seen:
            raise ValueError(f'第 {number} 行音频重复。')
        seen.add(path)
        text = str(row.get('text') or '').strip()
        if not text or len(text) > 2000:
            raise ValueError(f'第 {number} 行需要 1–2000 字符的准确文本。')
    return True


def import_batch(manager, rows, reference_index=0):
    """All-or-nothing clip import, including audio conversion failures."""
    import copy
    validate_rows(rows)
    if not 0 <= reference_index < len(rows):
        raise ValueError('请选择参考音频。')
    manager._ensure_idle()
    original = copy.deepcopy(manager.current_project())
    folder = manager.project_dir()
    created = []
    # Keep the previous reference bytes until the entire operation succeeds.
    reference = folder / 'reference.wav'
    old_reference = reference.read_bytes() if reference.exists() else None
    generated = [folder / name for name in ['train_raw.jsonl', 'train_with_codes.jsonl']]
    old_generated = {p: p.read_bytes() if p.exists() else None for p in generated}
    try:
        for row in rows:
            clip = manager.add_clip(row['audio'], row['text'])
            created.append(folder / clip['audio'])
        row = rows[reference_index]
        manager.set_reference(row['audio'], row['text'])
        result = manager.build_raw()
        return result
    except Exception:
        # Catch even an add that saved metadata before raising.
        old_audio = {item['audio'] for item in original['clips']}
        for clip in manager.current_project()['clips']:
            if clip['audio'] not in old_audio:
                created.append(folder / clip['audio'])
        for path in set(created):
            path.unlink(missing_ok=True)
        if old_reference is None:
            reference.unlink(missing_ok=True)
        else:
            reference.write_bytes(old_reference)
        for path, payload in old_generated.items():
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)
        manager._save_project(original)
        raise
