"""mpv integration: playback, progress tracking and downloads."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from typing import NamedTuple

from yt_tui import utils

logger = logging.getLogger(__name__)

# Formatos de calidad disponibles (se pasan a mpv como --ytdl-format)
QUALITY_FORMATS: dict[str, str] = {
    "best": "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
}


class MpvResult(NamedTuple):
    """Resultado de una reproducción con mpv."""

    returncode: int
    error: str = ""


def find_tool(name: str) -> str | None:
    """Find an executable in PATH or common Windows locations."""
    which_path = shutil.which(name)
    if which_path:
        return which_path

    if sys.platform == "win32":
        # Search common Windows paths
        user_profile = os.environ.get("USERPROFILE", "")
        local_appdata = os.environ.get("LOCALAPPDATA", "")

        paths = [
            os.path.join(user_profile, "scoop", "shims"),
            os.path.join(local_appdata, "Microsoft", "WinGet", "Packages"),
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "mpv"),
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "MPV Player"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "mpv"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "MPV Player"),
        ]

        for p in paths:
            for ext in (".com", ".exe"):
                exe = os.path.join(p, f"{name}{ext}")
                if os.path.exists(exe):
                    return exe
    return None


# ── Progreso / "continuar viendo" ─────────────────────────────────────────────

_PROGRESS_LUA_SOURCE = r"""-- YT-TUI: guarda la posición de reproducción al salir.
local opts = require 'mp.options'
local o = { file = "", key = "" }
opts.read_options(o, "yt_tui_progress")

local function save()
    if o.file == "" or o.key == "" then return end
    local pos = mp.get_property_number("time-pos")
    local dur = mp.get_property_number("duration")
    if pos == nil or dur == nil or dur <= 0 then return end
    local f = io.open(o.file, "a")
    if f == nil then return end
    f:write(string.format('{"key":"%s","pos":%.2f,"dur":%.2f}\n', o.key, pos, dur))
    f:close()
end

mp.register_event("end-file", save)
mp.register_event("shutdown", save)
"""


def _progress_lua() -> pathlib.Path:
    return utils.DATA_DIR / "mpv_progress.lua"


def _progress_json() -> pathlib.Path:
    return utils.DATA_DIR / "progress.json"


def _progress_jsonl() -> pathlib.Path:
    return utils.DATA_DIR / "progress.jsonl"


def ensure_progress_script() -> str:
    """Crea (si hace falta) el script Lua que guarda la posición."""
    utils.ensure_data_dir()
    path = _progress_lua()
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != _PROGRESS_LUA_SOURCE:
            path.write_text(_PROGRESS_LUA_SOURCE, encoding="utf-8")
    except OSError:
        logger.exception("No se pudo escribir el script de progreso de mpv")
    return str(path)


def consume_progress() -> dict:
    """Lee las posiciones que dejó mpv y las guarda en progress.json."""
    jsonl = _progress_jsonl()
    if not jsonl.exists():
        return {}
    try:
        lines = jsonl.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    latest: dict[str, dict] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("key")
        if not key:
            continue
        latest[str(key)] = {
            "pos": float(rec.get("pos") or 0),
            "dur": float(rec.get("dur") or 0),
        }

    try:
        jsonl.write_text("", encoding="utf-8")
    except OSError:
        pass

    if not latest:
        return {}

    data = utils.load_json(_progress_json())
    if not isinstance(data, dict):
        data = {}
    data.update(latest)
    utils.save_json(_progress_json(), data)
    return latest


def progress_info(video_id: str) -> dict | None:
    """Devuelve {'pos', 'dur', 'percent'} de un vídeo, o None."""
    if not video_id:
        return None
    data = utils.load_json(_progress_json())
    if not isinstance(data, dict):
        return None
    rec = data.get(video_id)
    if not isinstance(rec, dict):
        return None
    dur = float(rec.get("dur") or 0)
    pos = float(rec.get("pos") or 0)
    if dur <= 0:
        return None
    percent = max(0, min(100, int(pos / dur * 100)))
    return {"pos": pos, "dur": dur, "percent": percent}


def resume_start(video_id: str) -> float:
    """Minuto desde el que reanudar (0 si es muy pronto o ya casi termina)."""
    info = progress_info(video_id)
    if not info:
        return 0.0
    pos, dur = info["pos"], info["dur"]
    if pos < 30 or pos > dur - 30:
        return 0.0
    return pos


# ── Comando de mpv ────────────────────────────────────────────────────────────

def _build_mpv_cmd(
    url: str,
    *,
    audio_only: bool = False,
    ytdl_path: str | None = None,
    title: str | None = None,
    quality: str = "best",
    speed: float | str = 1.0,
    volume: int | str = 100,
    subtitles: bool = False,
    start: float = 0.0,
    audio_lang: str = "",
    sub_lang: str = "",
    progress_key: str | None = None,
) -> list[str]:
    """Construye el comando de mpv a partir de las preferencias."""
    mpv_path = find_tool("mpv")
    if not mpv_path:
        raise RuntimeError("mpv not found")

    cmd = [
        mpv_path,
        "--quiet",           # muestra los errores (antes --really-quiet los ocultaba)
        "--no-config",       # ignora mpv.conf del usuario para evitar sorpresas
        "--hwdec=auto-safe",  # aceleración por hardware cuando sea posible
        "--cache=yes",       # búfer para conexiones inestables
    ]

    # Volumen inicial (0-130). mpv usa 100 por defecto; lo pasamos siempre
    # para que el nivel sea el mismo en todos los vídeos.
    try:
        vol = int(volume)
    except (TypeError, ValueError):
        vol = 100
    cmd.append(f"--volume={max(0, min(130, vol))}")

    script_opts: list[str] = []
    if ytdl_path:
        script_opts.append(f"ytdl_hook-ytdl_path={ytdl_path}")
    if progress_key:
        ensure_progress_script()
        script_opts.append(f"yt_tui_progress-file={_progress_jsonl()}")
        script_opts.append(f"yt_tui_progress-key={progress_key}")
        cmd.append(f"--script={_progress_lua()}")
    if script_opts:
        cmd.append("--script-opts=" + ",".join(script_opts))

    if audio_lang:
        cmd.append(f"--alang={audio_lang}")
    if sub_lang:
        cmd.append(f"--slang={sub_lang}")

    fmt = QUALITY_FORMATS.get(quality)
    if fmt and quality != "best":
        cmd.append(f"--ytdl-format={fmt}")

    if subtitles:
        cmd.append("--sub-auto=fuzzy")
        cmd.append("--ytdl-raw-options=write-subs=,write-auto-subs=")

    if audio_only:
        cmd.extend([
            "--no-video",
            "--force-window=immediate",
            "--autofit=520x260",
        ])

    try:
        spd = float(speed)
    except (TypeError, ValueError):
        spd = 1.0
    if spd != 1.0:
        cmd.append(f"--speed={spd:g}")

    try:
        start_pos = float(start)
    except (TypeError, ValueError):
        start_pos = 0.0
    if start_pos > 0:
        cmd.append(f"--start={start_pos:.2f}")

    if title:
        cmd.append(f"--title={title}")

    cmd.append(url)
    return cmd


def play(
    url: str,
    audio_only: bool = False,
    ytdl_path: str | None = None,
    title: str | None = None,
    **opts,
) -> int:
    """Spawn mpv for *url*, blocking until the user quits.

    Returns the exit code of mpv.
    """
    cmd = _build_mpv_cmd(
        url, audio_only=audio_only, ytdl_path=ytdl_path, title=title, **opts
    )
    result = subprocess.run(cmd)
    return result.returncode


async def play_async(
    url: str,
    audio_only: bool = False,
    ytdl_path: str | None = None,
    title: str | None = None,
    **opts,
) -> MpvResult:
    """Spawn mpv asynchronously, returning an MpvResult with any error text."""
    cmd = _build_mpv_cmd(
        url, audio_only=audio_only, ytdl_path=ytdl_path, title=title, **opts
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise

    error = ""
    # El código 4 significa que el usuario cerró mpv; no es un error.
    if proc.returncode not in (0, 4):
        error = stderr.decode(errors="replace").strip()
        logger.error("mpv falló (código %s): %s", proc.returncode, error)

    return MpvResult(proc.returncode, error)


def download(
    url: str,
    dest: str | None = None,
    quality: str = "best",
    audio_only: bool = False,
) -> str:
    """Descarga *url* con yt-dlp (compatibilidad; la UI usa DownloadManager)."""
    import yt_dlp

    out_dir = pathlib.Path(dest) if dest else pathlib.Path.home() / "Downloads" / "yt-tui"
    out_dir.mkdir(parents=True, exist_ok=True)

    opts: dict = {
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "continuedl": True,
    }
    if audio_only or quality == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts.update({
            "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
            "merge_output_format": "mp4",
        })

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)
