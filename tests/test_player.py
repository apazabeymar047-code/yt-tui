"""Tests for player.py"""
import subprocess
from unittest.mock import patch

import pytest

from yt_tui.player import play

URL = "https://www.youtube.com/watch?v=abc123"


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_calls_mpv(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL)
    cmd = mock_run.call_args[0][0]
    assert any("mpv" in str(part) for part in cmd)
    assert URL in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_audio_only(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, audio_only=True)
    cmd = mock_run.call_args[0][0]
    assert "--no-video" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_user_quit_no_error(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 4)
    play(URL)  # should not raise


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_bad_exit_returns_code(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 1)
    exit_code = play(URL)
    assert exit_code == 1


@patch("yt_tui.player.shutil.which", return_value=None)
@patch("yt_tui.player.os.path.exists", return_value=False)
def test_play_no_mpv_raises_runtime_error(mock_exists, mock_which):
    with pytest.raises(RuntimeError, match="mpv not found"):
        play(URL)


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_stability_flags(mock_run, mock_which):
    """Los flags de estabilidad siempre van presentes."""
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL)
    cmd = mock_run.call_args[0][0]
    assert "--no-config" in cmd
    assert "--hwdec=auto-safe" in cmd
    assert "--cache=yes" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_quality(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, quality="720p")
    cmd = mock_run.call_args[0][0]
    assert any(part.startswith("--ytdl-format=") and "720" in part for part in cmd)


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_best_quality_omits_format(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, quality="best")
    cmd = mock_run.call_args[0][0]
    assert not any(part.startswith("--ytdl-format=") for part in cmd)


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_speed(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, speed=1.5)
    cmd = mock_run.call_args[0][0]
    assert "--speed=1.5" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_speed_normal_omitted(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, speed=1.0)
    cmd = mock_run.call_args[0][0]
    assert not any(part.startswith("--speed=") for part in cmd)


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_volume(mock_run, mock_which):
    """El volumen elegido se pasa siempre a mpv."""
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, volume=70)
    cmd = mock_run.call_args[0][0]
    assert "--volume=70" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_volume_default(mock_run, mock_which):
    """Sin preferencia, mpv usa 100 (el nivel de siempre)."""
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL)
    cmd = mock_run.call_args[0][0]
    assert "--volume=100" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_volume_clamped_and_safe(mock_run, mock_which):
    """Valores raros no rompen el comando: se limitan a 0-130."""
    mock_run.return_value = subprocess.CompletedProcess([], 0)

    play(URL, volume=999)
    cmd = mock_run.call_args[0][0]
    assert "--volume=130" in cmd

    play(URL, volume=-20)
    cmd = mock_run.call_args[0][0]
    assert "--volume=0" in cmd

    play(URL, volume="no-numero")
    cmd = mock_run.call_args[0][0]
    assert "--volume=100" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_subtitles_and_languages(mock_run, mock_which):
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, subtitles=True, start=120.0, audio_lang="es", sub_lang="en")
    cmd = mock_run.call_args[0][0]
    assert "--sub-auto=fuzzy" in cmd
    assert any(part.startswith("--start=") and "120" in part for part in cmd)
    assert "--alang=es" in cmd
    assert "--slang=en" in cmd


@patch("yt_tui.player.shutil.which", return_value="/usr/bin/mpv")
@patch("yt_tui.player.subprocess.run")
def test_play_progress_key_adds_script(mock_run, mock_which, tmp_path, monkeypatch):
    monkeypatch.setattr("yt_tui.utils.DATA_DIR", tmp_path)
    mock_run.return_value = subprocess.CompletedProcess([], 0)
    play(URL, progress_key="abc123")
    cmd = mock_run.call_args[0][0]
    assert any(part.startswith("--script=") for part in cmd)
    assert any("yt_tui_progress-key=abc123" in part for part in cmd)
