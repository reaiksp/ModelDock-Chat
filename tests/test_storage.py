import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from chatdock import storage

class Task:
    def check(self): pass

@pytest.fixture(autouse=True)
def isolated(monkeypatch,tmp_path):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    monkeypatch.setattr(storage,'_set_environment',lambda *args:None)


def test_copy_verified_keeps_source_and_reuses_equal_target(tmp_path):
    source=tmp_path/'source';target=tmp_path/'target';source.mkdir();target.mkdir()
    (source/'weights.gguf').write_bytes(b'known weights')
    first=storage.copy_verified(source,target,Task(),lambda text:None)
    assert first and (source/'weights.gguf').read_bytes()==(target/'weights.gguf').read_bytes()
    assert storage.copy_verified(source,target,Task(),lambda text:None)==first


def test_different_target_is_never_overwritten(tmp_path):
    source=tmp_path/'source';target=tmp_path/'target';source.mkdir();target.mkdir()
    (source/'model').write_bytes(b'original');(target/'model').write_bytes(b'other')
    with pytest.raises(ValueError,match='未覆盖'):
        storage.copy_verified(source,target,Task(),lambda text:None)
    assert (source/'model').read_bytes()==b'original' and (target/'model').read_bytes()==b'other'


def test_nested_directories_are_refused(tmp_path):
    source=tmp_path/'source';source.mkdir()
    with pytest.raises(ValueError,match='相互包含'):
        storage.copy_verified(source,source/'nested',Task(),lambda text:None)


def test_cancellation_removes_only_own_incomplete_file(tmp_path):
    source=tmp_path/'source';target=tmp_path/'target';source.mkdir();target.mkdir()
    (source/'model').write_bytes(b'A'*100)
    class Cancel:
        count=0
        def check(self):
            self.count+=1
            if self.count==3:raise RuntimeError('cancel')
    with pytest.raises(RuntimeError):storage.copy_verified(source,target,Cancel(),lambda text:None)
    assert (source/'model').read_bytes()==b'A'*100
    assert not (target/'model').exists()


def test_persistent_paths_are_shared_and_independent_of_stale_environment(tmp_path,monkeypatch):
    target=tmp_path/'chosen'
    storage._save('text_models',target)
    storage._save('speech_models',tmp_path/'voices')
    monkeypatch.setenv('OLLAMA_MODELS',str(tmp_path/'stale'))
    assert storage.text_models_dir()==target
    assert storage.speech_models_dir(tmp_path/'runtime')==tmp_path/'voices'


def test_directory_root_is_not_a_model_folder(tmp_path):
    with pytest.raises(ValueError):storage.validate_directory(tmp_path.anchor)
    with pytest.raises(ValueError):storage.validate_directory('relative/models')


def test_changed_source_during_copy_is_detected(tmp_path):
    source=tmp_path/'source';target=tmp_path/'target';source.mkdir();target.mkdir()
    (source/'model').write_bytes(b'old')
    def log(text): (source/'model').write_bytes(b'changed')
    with pytest.raises(ValueError,match='变化'):
        storage.copy_verified(source,target,Task(),log)
    assert (source/'model').exists()


def fake_server(monkeypatch,source):
    state={'active':source,'stopped':0,'launched':[]}
    process=SimpleNamespace(exe=lambda:'ollama.exe')
    monkeypatch.setattr(storage,'_listener',lambda:process)
    monkeypatch.setattr(storage,'active_text_dir',lambda:state['active'])
    monkeypatch.setattr(storage,'_api',lambda *args:{'models':[{'name':'test','digest':'same'}]})
    monkeypatch.setattr(storage,'_require_idle',lambda:None)
    def stop(proc):state['stopped']+=1
    monkeypatch.setattr(storage,'_stop',stop)
    def launch(cli,path):
        state['active']=path;state['launched'].append(path)
        return SimpleNamespace(poll=lambda:None,terminate=lambda:None,wait=lambda **kw:None)
    monkeypatch.setattr(storage,'_launch',launch)
    return state


def test_service_restart_and_persistence_follow_verified_copy(tmp_path,monkeypatch):
    source=tmp_path/'old';source.mkdir();(source/'model').write_bytes(b'model')
    target=tmp_path/'new';state=fake_server(monkeypatch,source)
    result=storage.apply_text_directory(target,Task(),lambda text:None)
    assert state['stopped']==1 and state['active']==target
    assert storage.text_models_dir()==target and result['models']==1
    assert (source/'model').exists() and (target/'model').read_bytes()==b'model'


def test_failed_new_server_rolls_back_without_deleting_models(tmp_path,monkeypatch):
    source=tmp_path/'old';source.mkdir();(source/'model').write_bytes(b'model')
    target=tmp_path/'new';state=fake_server(monkeypatch,source)
    launch=storage._launch
    def fail_first(cli,path):
        if path==target:raise RuntimeError('failed new server')
        return launch(cli,path)
    monkeypatch.setattr(storage,'_launch',fail_first)
    with pytest.raises(RuntimeError):storage.apply_text_directory(target,Task(),lambda text:None)
    assert state['active']==source and 'text_models' not in storage.settings()
    assert (source/'model').exists()


def test_active_request_prevents_restart(tmp_path,monkeypatch):
    source=tmp_path/'old';source.mkdir();state=fake_server(monkeypatch,source)
    def busy():raise ValueError('busy')
    monkeypatch.setattr(storage,'_require_idle',busy)
    with pytest.raises(ValueError):storage.apply_text_directory(tmp_path/'new',Task(),lambda text:None)
    assert state['stopped']==0 and not storage.settings()


def test_speech_migration_leaves_runtime_and_profile_untouched(tmp_path):
    models=tmp_path/'models';models.mkdir();(models/'model.complete').write_text('ok')
    profile=tmp_path/'chat.db';profile.write_bytes(b'private')
    target=tmp_path/'voice-disk'
    assert storage.apply_speech_directory(target,models,Task(),lambda text:None)==str(target)
    assert profile.read_bytes()==b'private' and (models/'model.complete').exists()
    assert storage.speech_models_dir(tmp_path)==target


def test_separate_speech_and_runtime_disks_are_budgeted_individually():
    from chatdock.hardware import Hardware,recommend
    hw=Hardware(128,110,24,13,100,'GPU',100,'C:/runtime','E:/voice-models','E:/text-models')
    plan=recommend(hw,voice=True,size='0.6B',usage='compressed',compression_level=1,conversation_demand=1,parameter_limit=.5)
    assert plan is not None and plan['voice']=='0.6B'
    hw.disk=11.9
    assert recommend(hw,voice=True,size='0.6B',usage='compressed',compression_level=1,parameter_limit=.5) is None


def test_speech_uses_selected_weight_folder_without_moving_runtime(tmp_path,monkeypatch):
    from chatdock.speech import SpeechService
    target=tmp_path/'other-disk-models'
    storage._save('speech_models',target)
    service=SpeechService(tmp_path/'profile')
    assert service.models_dir==target
    assert service.root==tmp_path/'profile/speech'
    assert service.python.is_relative_to(service.root)
