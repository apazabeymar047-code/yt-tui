"""Tests for progress tracking ("continuar viendo")."""
from yt_tui import player


def test_consume_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    (tmp_path / "progress.jsonl").write_text(
        '{"key":"abc","pos":300.0,"dur":600.0}\n', encoding="utf-8"
    )

    result = player.consume_progress()
    assert result["abc"]["pos"] == 300.0
    assert result["abc"]["dur"] == 600.0

    info = player.progress_info("abc")
    assert info is not None
    assert info["percent"] == 50
    assert player.resume_start("abc") == 300.0

    # El fichero temporal queda vacío tras consumirlo
    assert (tmp_path / "progress.jsonl").read_text(encoding="utf-8") == ""


def test_resume_start_rules(tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)

    # Muy al principio -> no reanuda
    (tmp_path / "progress.jsonl").write_text(
        '{"key":"early","pos":5.0,"dur":600.0}\n', encoding="utf-8"
    )
    player.consume_progress()
    assert player.resume_start("early") == 0.0

    # Casi terminado -> no reanuda
    (tmp_path / "progress.jsonl").write_text(
        '{"key":"almost","pos":590.0,"dur":600.0}\n', encoding="utf-8"
    )
    player.consume_progress()
    assert player.resume_start("almost") == 0.0

    # En medio -> reanuda
    (tmp_path / "progress.jsonl").write_text(
        '{"key":"middle","pos":250.0,"dur":600.0}\n', encoding="utf-8"
    )
    player.consume_progress()
    assert player.resume_start("middle") == 250.0

    # Vídeo desconocido
    assert player.progress_info("noexiste") is None
