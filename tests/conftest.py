import pytest
from chatdock import storage


@pytest.fixture(autouse=True)
def isolate_model_storage_settings(monkeypatch,tmp_path):
    # Real user model locations and services must not affect mocked tests.
    monkeypatch.setattr(storage,'config_dir',lambda:tmp_path/'shared-model-storage')
