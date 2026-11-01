import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main, model_storage
from backend.paraformer_asr import ParaformerTranscriber
from backend.whisper_asr import WhisperTranscriber


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path / "models"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(main, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(main, "DEFAULT_WHISPER_CACHE_DIR", root)
    monkeypatch.setattr(main, "WHISPER_CACHE_DIR", root)
    monkeypatch.setattr(main, "transcriber", WhisperTranscriber(root))
    monkeypatch.setattr(main, "paraformer_transcriber", ParaformerTranscriber(root))
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    return TestClient(main.app), root, workspace


def test_directory_change_persists_and_reset_keeps_old_files(models, tmp_path):
    client, default, workspace = models
    old = default / "keep.bin"
    old.write_bytes(b"old model")
    status = client.get("/api/whisper-models").json()
    assert Path(status["directory"]) == default
    assert Path(status["default_directory"]) == default
    custom = tmp_path / "custom models"
    changed = client.put("/api/whisper-models/directory", json={"directory": str(custom)})
    assert changed.status_code == 200
    assert main.transcriber.download_root == custom
    assert main.paraformer_transcriber.download_root == custom / "sherpa"
    assert model_storage.load_directory(workspace, default) == custom
    reset = client.put("/api/whisper-models/directory", json={"directory": ""})
    assert reset.status_code == 200
    assert model_storage.load_directory(workspace, default) == default
    assert old.read_bytes() == b"old model"
    assert custom.is_dir()


def test_invalid_directory_does_not_replace_settings(models, tmp_path):
    client, default, workspace = models
    invalid = tmp_path / "file"
    invalid.write_text("keep")
    for value in ("relative/path", str(invalid)):
        result = client.put("/api/whisper-models/directory", json={"directory": value})
        assert result.status_code == 422
    assert main.WHISPER_CACHE_DIR == default
    assert not (workspace / model_storage.SETTINGS_FILE).exists()


@pytest.mark.parametrize("model", ["base", "paraformer-zh"])
def test_delete_only_selected_model_including_partial_download(models, model):
    client, root, _workspace = models
    repo = main.transcriber._model_repo(model) if model == "base" else None
    targets = model_storage.model_directories(root, model, repo)
    for folder in targets:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "partial.tmp").write_bytes(b"unfinished")
    other = root / "manual" / "tiny"
    other.mkdir(parents=True)
    (other / "keep.bin").write_bytes(b"keep")
    response = client.delete(f"/api/whisper-models/{model}")
    assert response.status_code == 200
    assert set(response.json()["deleted_paths"]) == {str(path) for path in targets}
    assert all(not path.exists() for path in targets)
    assert (other / "keep.bin").read_bytes() == b"keep"


@pytest.mark.parametrize("status", ["pending", "queued", "processing", "cancelling"])
def test_running_tasks_block_mutations(models, tmp_path, status):
    client, root, workspace = models
    main.tasks["busy"] = {"status": status}
    assert client.delete("/api/whisper-models/base").status_code == 409
    assert client.put("/api/whisper-models/directory", json={"directory": str(tmp_path / "next")}).status_code == 409
    assert main.WHISPER_CACHE_DIR == root
    assert not (workspace / model_storage.SETTINGS_FILE).exists()


def test_deletion_rejects_escape_or_root_before_removing_anything(tmp_path):
    root = tmp_path / "models"
    folder = root / "manual" / "base"
    folder.mkdir(parents=True)
    (folder / "keep").write_text("keep")
    for unsafe in (root, tmp_path / "outside"):
        with pytest.raises(ValueError):
            model_storage.validate_deletion(root, [folder, unsafe])
        assert (folder / "keep").is_file()


def test_invalid_model_id_cannot_delete_a_directory(models):
    client, root, _workspace = models
    assert client.delete("/api/whisper-models/not-a-model").status_code == 422
    assert root.is_dir()


@pytest.mark.asyncio
async def test_cancelled_ui_task_cannot_delete_models_while_worker_is_exiting(monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def transcribe(*args, **kwargs):
        started.set()
        await finish.wait()
        return {}

    monkeypatch.setattr(main, "run_transcription_local", transcribe)
    monkeypatch.setattr(main.asr_process, "use_child_process", lambda: False)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    monkeypatch.setattr(main, "_active_transcriptions", set())
    parent = asyncio.create_task(main.run_transcription(Path("test.wav"), "base", False, None, None))
    await started.wait()
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    with pytest.raises(main.HTTPException) as error:
        main.require_models_idle()
    assert error.value.status_code == 409
    finish.set()
    await asyncio.gather(*main._active_transcriptions)
    main.require_models_idle()


@pytest.mark.parametrize("model", ["tiny.en", "base.en", "small.en", "medium.en"])
def test_english_models_cannot_be_selected_or_downloaded(models, model):
    client, root, _ = models
    status = client.get("/api/whisper-models")
    assert model not in {item["id"] for item in status.json()["models"]}
    imported = client.post("/api/whisper-models/manual-folder", json={"model": model, "open": False})
    assert imported.status_code == 422
    assert not (root / "manual" / model).exists()
    from backend.whisper_asr import WhisperTranscriber
    from backend.whisper_vulkan import MODELS
    assert model not in MODELS
    with pytest.raises(ValueError, match="英语专用模型已移除"):
        WhisperTranscriber._model_repo(model)
