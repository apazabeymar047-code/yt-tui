"""Tests for the download manager."""
import time

import pytest

from yt_tui import downloads


def _wait_idle(manager, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.status in ("queued", "downloading") for t in manager.tasks()):
            return True
        time.sleep(0.05)
    return False


def test_add_and_complete(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    manager = downloads.DownloadManager()
    seen = []

    def fake_download(task):
        seen.append((task.id, task.quality))

    monkeypatch.setattr(manager, "_download", fake_download)

    task = manager.add("https://x/y", "Mi vídeo", "720p")
    assert task.status in ("queued", "downloading")
    assert _wait_idle(manager)

    tasks = manager.tasks()
    assert len(tasks) == 1
    assert tasks[0].status == "completed"
    assert tasks[0].progress == 100.0
    assert seen == [(task.id, "720p")]


def test_error_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    manager = downloads.DownloadManager()

    def boom(task):
        raise RuntimeError("falló la red")

    monkeypatch.setattr(manager, "_download", boom)
    manager.add("https://x/y", "Falla", "best")
    assert _wait_idle(manager)

    task = manager.tasks()[0]
    assert task.status == "error"
    assert "falló la red" in task.error

    # Reanudar vuelve a ponerla en cola y termina bien
    monkeypatch.setattr(manager, "_download", lambda task: None)
    assert manager.resume_all() == 1
    assert _wait_idle(manager)
    assert manager.tasks()[0].status == "completed"


def test_persistence_marks_interrupted_as_queued(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    # Simulamos un estado guardado con una descarga "a medias"
    from yt_tui import utils
    utils.save_json(tmp_path / "downloads.json", [{
        "id": "abcd1234",
        "title": "Cortada",
        "url": "https://x/y",
        "quality": "best",
        "status": "downloading",
        "progress": 42.0,
        "created": 1.0,
    }])

    manager = downloads.DownloadManager()
    tasks = manager.tasks()
    assert len(tasks) == 1
    assert tasks[0].status == "queued"


def test_remove_does_not_remove_active(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    manager = downloads.DownloadManager()

    task = downloads.DownloadTask(id="x1", title="t", url="u", status="downloading")
    manager._tasks.append(task)
    assert manager.remove("x1") is False

    task.status = "completed"
    assert manager.remove("x1") is True
    assert manager.tasks() == []
