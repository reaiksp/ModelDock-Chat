import json
import sys
from pathlib import Path

import pytest

from chatdock.voice_training import VoiceTrainingError, VoiceTrainingManager


def _write_wav(path: Path, payload_size=100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + b"\0" * payload_size)


def _normalizer(durations):
    values = iter(durations)

    def normalize(source, output, max_seconds):
        _write_wav(Path(output))
        return {"duration": next(values), "sampleRate": 24000, "peak": 0.95, "rms": 0.1}

    return normalize


def _manager(tmp_path, durations=(1.2, 10.0), **kwargs):
    return VoiceTrainingManager(
        root=tmp_path / "training",
        active_project="k_voice",
        normalizer=_normalizer(durations),
        tts_python=tmp_path / "tts-python.exe",
        finetuning_dir=tmp_path / "finetuning",
        runner_script=Path(__file__).parents[1] / "chatdock/voice_training_runner.py",
        worker_script=Path(__file__).parents[1] / "chatdock/voice_training_worker.py",
        **kwargs,
    )


def test_project_create_active_and_list(tmp_path):
    manager = _manager(tmp_path)
    assert manager.active_name == "k_voice"
    assert (tmp_path / "training" / "k_voice" / "project.json").is_file()

    created = manager.create_project("Second", activate=True)
    assert created["name"] == "Second"
    assert manager.active_name == "Second"
    rows = manager.list_projects()
    assert {row["name"] for row in rows} == {"k_voice", "Second"}
    assert next(row for row in rows if row["name"] == "Second")["active"] is True


def test_add_clip_reference_and_build_raw(tmp_path):
    manager = _manager(tmp_path, durations=(1.3, 10.0))
    source = tmp_path / "source.wav"
    _write_wav(source)

    clip = manager.add_clip(source, "第一条训练文本")
    assert clip["id"] == 1
    assert clip["duration"] == 1.3
    assert (manager.project_dir() / clip["audio"]).is_file()

    ref = manager.set_reference(source, "这是参考文本")
    assert ref["duration"] == 10.0
    assert (manager.project_dir() / "reference.wav").is_file()
    assert ref["warning"] == ""

    info = manager.build_raw()
    raw = manager.project_dir() / "train_raw.jsonl"
    rows = [json.loads(line) for line in raw.read_text("utf-8").splitlines() if line.strip()]
    assert info["count"] == 1 and len(rows) == 1
    assert rows[0]["text"] == "第一条训练文本"
    assert Path(rows[0]["audio"]).is_absolute()
    assert Path(rows[0]["ref_audio"]).is_absolute()

    project = json.loads((manager.project_dir() / "project.json").read_text("utf-8"))
    assert project["reference"]["audio"] == "reference.wav"
    assert project["clips"][0]["text"] == "第一条训练文本"
    assert project["raw"]["sha256"] == info["sha256"]


def test_short_clip_and_short_reference_are_rejected(tmp_path):
    source = tmp_path / "source.wav"
    _write_wav(source)

    manager = _manager(tmp_path, durations=(0.5,))
    with pytest.raises(VoiceTrainingError):
        manager.add_clip(source, "太短")
    assert manager.clips() == []

    manager = _manager(tmp_path / "second", durations=(1.0,))
    with pytest.raises(VoiceTrainingError):
        manager.set_reference(source, "太短")


def _checkpoint(manager, name="checkpoint-epoch-0", speaker="k_voice", base="0.6b"):
    run = manager.project_dir() / "runs" / "run-test"
    path = run / "output" / name
    path.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run": "run-test", "base": base}), "utf-8")
    (path / "config.json").write_text(json.dumps({
        "tts_model_type": "custom_voice",
        "talker_config": {"spk_id": {speaker: 3000}},
    }), "utf-8")
    (path / "model.safetensors").write_bytes(b"dummy")
    return path


def test_resolve_checkpoint_and_deploy_plan(tmp_path):
    manager = _manager(tmp_path)
    first = _checkpoint(manager, "checkpoint-epoch-0")
    second = _checkpoint(manager, "checkpoint-epoch-1", speaker="other")
    rows = manager.checkpoints()
    assert len(rows) == 2
    assert manager.resolve_checkpoint("1") in {first.resolve(), second.resolve()}
    assert manager.resolve_checkpoint("checkpoint-epoch-0") == first.resolve()
    assert manager.resolve_checkpoint(str(first)) == first.resolve()

    plan = manager.deploy_plan("checkpoint-epoch-0")
    assert plan["speaker"] == "k_voice"
    assert plan["modelPath"] == str(first.resolve())
    assert plan["repo"].endswith("Finetuned")
    assert plan["extraSpeakers"] == ["k_voice"]


def test_start_training_and_done_status(tmp_path):
    manager = _manager(tmp_path)
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.json").write_text("{}", "utf-8")
    manager.base_paths = {"0.6b": base, "1.7b": base}
    manager.tts_python.write_bytes(b"x")
    manager.finetuning_dir.mkdir()
    (manager.finetuning_dir / "sft_12hz_win.py").write_text("# dummy", "utf-8")

    prepared = manager.project_dir() / "train_with_codes.jsonl"
    prepared.write_text('{"audio": "x", "text": "x", "ref_audio": "r", "audio_codes": [[1]]}\n', "utf-8")
    manager.prepare_codes = lambda base="0.6b": {
        "path": str(prepared), "rawSha256": "raw", "preparedSha256": "prep",
        "count": 1, "cached": True,
    }
    calls = []

    class Proc:
        pid = 4321

        def poll(self):
            return None

    def popen(command, **kwargs):
        calls.append(command)
        return Proc()

    manager._popen = popen
    info = manager.start_training(base="0.6b", epochs=3, batch_size=1,
                                  lr=2e-6, speaker="k_voice")
    assert info["status"] == "running" and info["runnerPid"] == 4321
    assert any("--gradient_checkpointing" in str(part) for part in calls[0])
    assert any("--speaker_name" in str(part) for part in calls[0])
    assert manager.current_project()["training"]["status"] == "running"

    # Find the actual run dir from the saved training metadata.
    done_path = Path(manager.current_project()["training"]["donePath"])
    done_path.write_text(json.dumps({"exitCode": 0, "finished": 1, "durationSec": 2}), "utf-8")
    status = manager.training_status()
    assert status["status"] == "completed"
    assert status["runnerAlive"] is False


def test_evaluate_uses_worker_json_without_training(tmp_path):
    manager = _manager(tmp_path)
    manager.tts_python.write_bytes(b"x")
    checkpoint = _checkpoint(manager)
    output_holder = {}

    def fake_worker(command, timeout):
        output = Path(command[command.index("--output") + 1])
        _write_wav(output, 200)
        output_holder["path"] = output
        return {"sampleRate": 24000, "duration": 1.2, "modelType": "custom_voice"}

    manager._worker_json = fake_worker
    info = manager.evaluate(checkpoint, "测试一句")
    assert info["output"] == str(output_holder["path"])
    assert info["speaker"] == "k_voice"
    assert output_holder["path"].is_file()

def test_prepare_codes_writes_ready_metadata(tmp_path):
    manager = _manager(tmp_path, durations=(1.2, 10.0))
    source = tmp_path / "source.wav"
    _write_wav(source)
    manager.add_clip(source, "测试文本")
    manager.set_reference(source, "参考文本")
    manager.tts_python.write_bytes(b"x")
    manager.finetuning_dir.mkdir()
    (manager.finetuning_dir / "prepare_data.py").write_text("# dummy", "utf-8")
    base = tmp_path / "base"
    (base / "speech_tokenizer").mkdir(parents=True)
    manager.base_paths = {"0.6b": base, "1.7b": base}

    def fake_run(command, **kwargs):
        input_path = Path(command[command.index("--input_jsonl") + 1])
        output_path = Path(command[command.index("--output_jsonl") + 1])
        rows = [json.loads(line) for line in input_path.read_text("utf-8").splitlines() if line.strip()]
        for row in rows:
            row["audio_codes"] = [[1, 2, 3]]
        output_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", "utf-8")
        return type("Result", (), {"returncode": 0})()

    manager._run_process = fake_run
    info = manager.prepare_codes("0.6b")
    assert info["status"] == "ready" and info["count"] == 1
    assert Path(info["path"]).is_file()
    project = json.loads((manager.project_dir() / "project.json").read_text("utf-8"))
    assert project["prepared"]["status"] == "ready"
    assert project["prepared"]["rawSha256"] == project["raw"]["sha256"]


def test_long_clip_and_reference_are_not_rejected(tmp_path):
    manager = _manager(tmp_path, durations=(31.0, 45.0))
    source = tmp_path / "source.wav"
    _write_wav(source)
    clip = manager.add_clip(source, "长片段")
    assert clip["duration"] == 31.0
    assert "超过 30 秒" in clip["warning"] and "不会拒绝" in clip["warning"]
    ref = manager.set_reference(source, "长参考")
    assert ref["duration"] == 45.0
    assert "超过 30 秒" in ref["warning"] and "不会拒绝" in ref["warning"]
    assert len(manager.clips()) == 1
