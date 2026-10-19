"""Tests for utils.py"""
import json
from pathlib import Path

import pytest

from yt_tui.utils import fmt_duration, fmt_views, load_json, save_json


@pytest.mark.parametrize("secs,expected", [
    (None, "--:--"),
    (0, "0:00"),
    (59, "0:59"),
    (60, "1:00"),
    (3661, "1:01:01"),
])
def test_fmt_duration(secs, expected):
    assert fmt_duration(secs) == expected


@pytest.mark.parametrize("count,expected", [
    (None, "N/A"),
    (999, "999"),
    (1_000, "1.0K"),
    (1_500, "1.5K"),
    (1_000_000, "1.0M"),
    (2_500_000, "2.5M"),
])
def test_fmt_views(count, expected):
    assert fmt_views(count) == expected


def test_load_json_missing(tmp_path):
    assert load_json(tmp_path / "nope.json") == []


def test_load_json_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    assert load_json(p) == []


def test_save_and_load_json(tmp_path, monkeypatch):
    from yt_tui.utils import DATA_DIR
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    p = tmp_path / "test.json"
    data = [{"id": "abc", "title": "Test"}]
    save_json(p, data)
    assert load_json(p) == data


def test_history_manager(tmp_path, monkeypatch):
    from yt_tui.utils import HistoryManager
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    
    # Test search history
    HistoryManager.add_search("python tutorial")
    HistoryManager.add_search("textual tui")
    history = HistoryManager.get_search_history()
    assert len(history) == 2
    assert history[0]["query"] == "textual tui"
    
    # Test duplicate search (moves to top)
    HistoryManager.add_search("python tutorial")
    history = HistoryManager.get_search_history()
    assert len(history) == 2
    assert history[0]["query"] == "python tutorial"
    
    # Test watch history
    video = {"id": "123", "title": "Great Video"}
    HistoryManager.add_watch(video)
    watch_history = HistoryManager.get_watch_history()
    assert len(watch_history) == 1
    assert watch_history[0]["id"] == "123"


def test_history_remove_and_clear(tmp_path, monkeypatch):
    from yt_tui.utils import HistoryManager
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    HistoryManager.add_search("uno")
    HistoryManager.add_search("dos")
    HistoryManager.remove_search("uno")
    assert [h["query"] for h in HistoryManager.get_search_history()] == ["dos"]

    HistoryManager.add_watch({"id": "1", "title": "A"})
    HistoryManager.add_watch({"id": "2", "title": "B"})
    HistoryManager.remove_watch("1")
    assert [h["id"] for h in HistoryManager.get_watch_history()] == ["2"]

    HistoryManager.clear_search()
    HistoryManager.clear_watch()
    assert HistoryManager.get_search_history() == []
    assert HistoryManager.get_watch_history() == []


def test_library_toggle(tmp_path, monkeypatch):
    from yt_tui.utils import Library
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    video = {"id": "abc", "title": "Ejemplo", "channel": "Canal"}

    assert Library.get("favorites") == []
    assert Library.toggle("favorites", video) is True
    assert Library.contains("favorites", "abc")
    assert [v["id"] for v in Library.get("favorites")] == ["abc"]

    # Segundo toggle lo quita
    assert Library.toggle("favorites", video) is False
    assert not Library.contains("favorites", "abc")

    # Las listas son independientes
    Library.toggle("later", video)
    assert Library.contains("later", "abc")
    assert not Library.contains("favorites", "abc")

    # Los vídeos sin id se ignoran
    assert Library.toggle("later", {"title": "sin id"}) is False


def test_queue_store(tmp_path, monkeypatch):
    from yt_tui.utils import QueueStore
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    assert QueueStore.get() == []
    assert QueueStore.add({"id": "a", "title": "A"}) is True
    # No se repite el mismo id
    assert QueueStore.add({"id": "a", "title": "A"}) is False
    QueueStore.add({"id": "b", "title": "B"})
    assert [v["id"] for v in QueueStore.get()] == ["a", "b"]

    QueueStore.remove_at(0)
    assert [v["id"] for v in QueueStore.get()] == ["b"]

    QueueStore.clear()
    assert QueueStore.get() == []

    # Sin id no se añade
    assert QueueStore.add({"title": "sin id"}) is False


def test_queue_store_remove_id_y_contains(tmp_path, monkeypatch):
    from yt_tui.utils import QueueStore
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    QueueStore.add({"id": "a", "title": "A"})
    assert QueueStore.contains("a") is True
    assert QueueStore.contains("b") is False

    assert QueueStore.remove_id("a") is True
    assert QueueStore.contains("a") is False
    # Quitar algo que no está no rompe nada
    assert QueueStore.remove_id("a") is False


def test_copy_to_clipboard_windows(monkeypatch):
    from yt_tui import utils

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return None

    monkeypatch.setattr(utils.subprocess, "run", fake_run)
    monkeypatch.setattr(utils.sys, "platform", "win32")

    assert utils.copy_to_clipboard("https://youtu.be/abc") is True
    assert calls and calls[0] == ["clip"]

    # Texto vacío no intenta copiar
    assert utils.copy_to_clipboard("") is False


def test_copy_to_clipboard_no_disponible(monkeypatch):
    from yt_tui import utils

    def boom(*args, **kwargs):
        raise OSError("sin portapapeles")

    monkeypatch.setattr(utils.subprocess, "run", boom)
    monkeypatch.setattr(utils.sys, "platform", "linux")

    assert utils.copy_to_clipboard("hola") is False
