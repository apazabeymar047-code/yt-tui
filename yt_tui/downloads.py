"""Gestor de descargas: cola, progreso, estados y reanudación."""
from __future__ import annotations

import pathlib
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

import yt_dlp

from yt_tui import utils

DOWNLOAD_DIR = pathlib.Path.home() / "Downloads" / "yt-tui"

QUALITY_LABELS: dict[str, str] = {
    "best": "Mejor",
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    "audio": "Solo audio (MP3)",
}

QUALITY_OPTIONS: list[tuple[str, str]] = [
    ("🎬  Mejor disponible", "best"),
    ("🎬  1080p", "1080p"),
    ("🎬  720p", "720p"),
    ("🎬  480p", "480p"),
    ("🎵  Solo audio (MP3)", "audio"),
]

FORMATS: dict[str, str] = {
    "best": "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
}

STATUS_LABELS: dict[str, str] = {
    "queued": "En cola",
    "downloading": "Descargando",
    "completed": "Completado",
    "error": "Error",
    "canceled": "Cancelado",
}


@dataclass
class DownloadTask:
    id: str
    title: str
    url: str
    quality: str = "best"
    status: str = "queued"
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    filepath: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)


class DownloadManager:
    """Cola de descargas secuencial con persistencia y reanudación."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._on_change = on_change
        self._lock = threading.RLock()
        self._tasks: list[DownloadTask] = []
        self._worker: threading.Thread | None = None
        self._last_notify = 0.0
        self._load()

    # ── Persistencia ─────────────────────────────────────────────────────────

    @staticmethod
    def _state_file() -> pathlib.Path:
        return utils.DATA_DIR / "downloads.json"

    def _load(self) -> None:
        raw = utils.load_json(self._state_file())
        if isinstance(raw, list):
            valid = set(DownloadTask.__dataclass_fields__)
            for item in raw:
                if not isinstance(item, dict):
                    continue
                kwargs = {k: v for k, v in item.items() if k in valid}
                try:
                    self._tasks.append(DownloadTask(**kwargs))
                except (TypeError, ValueError):
                    continue
        # Una descarga interrumpida al cerrar la app queda pendiente.
        for task in self._tasks:
            if task.status == "downloading":
                task.status = "queued"
                task.speed = ""
                task.eta = ""
        self._save()

    def _save(self) -> None:
        with self._lock:
            utils.save_json(
                self._state_file(),
                [asdict(task) for task in self._tasks],
            )

    # ── API pública ──────────────────────────────────────────────────────────

    def tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks)

    def add(self, url: str, title: str, quality: str = "best") -> DownloadTask:
        task = DownloadTask(
            id=uuid.uuid4().hex[:8],
            title=title or "Sin título",
            url=url,
            quality=quality,
        )
        with self._lock:
            self._tasks.insert(0, task)
        self._save()
        self._notify(force=True)
        self.start()
        return task

    def start(self) -> None:
        """Arranca el trabajador si hay tareas en cola."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if not any(t.status == "queued" for t in self._tasks):
                return
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def resume_all(self) -> int:
        """Vuelve a poner en cola las descargas pendientes o con error."""
        with self._lock:
            count = 0
            for task in self._tasks:
                if task.status in ("queued", "error"):
                    task.status = "queued"
                    task.error = ""
                    count += 1
        if count:
            self._save()
            self._notify(force=True)
            self.start()
        return count

    def remove(self, task_id: str) -> bool:
        with self._lock:
            task = next((t for t in self._tasks if t.id == task_id), None)
            if task is None or task.status == "downloading":
                return False
            self._tasks = [t for t in self._tasks if t.id != task_id]
        self._save()
        self._notify(force=True)
        return True

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [
                t for t in self._tasks
                if t.status not in ("completed", "canceled")
            ]
            removed = before - len(self._tasks)
        if removed:
            self._save()
            self._notify(force=True)
        return removed

    # ── Trabajador ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            with self._lock:
                # La lista está ordenada de más nueva a más antigua;
                # tomamos la más antigua en cola (FIFO).
                task = next(
                    (t for t in reversed(self._tasks) if t.status == "queued"),
                    None,
                )
                if task is None:
                    self._worker = None
                    return
                task.status = "downloading"
                task.progress = 0.0
                task.speed = ""
                task.eta = ""
                task.error = ""
            self._save()
            self._notify(force=True)

            try:
                self._download(task)
                with self._lock:
                    task.status = "completed"
                    task.progress = 100.0
                    task.speed = ""
                    task.eta = ""
            except Exception as exc:  # noqa: BLE001 - queremos mostrarlo en la UI
                with self._lock:
                    task.status = "error"
                    task.error = str(exc)[:300]
                    task.speed = ""
                    task.eta = ""
            self._save()
            self._notify(force=True)

    def _ydl_opts(self, task: DownloadTask) -> dict:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        opts: dict = {
            "outtmpl": str(DOWNLOAD_DIR / "%(title).150s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "progress_hooks": [lambda data: self._hook(task, data)],
        }
        if task.quality == "audio":
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
                "format": FORMATS.get(task.quality, FORMATS["best"]),
                "merge_output_format": "mp4",
            })
        return opts

    def _download(self, task: DownloadTask) -> None:
        with yt_dlp.YoutubeDL(self._ydl_opts(task)) as ydl:
            info = ydl.extract_info(task.url, download=True)
            if info:
                try:
                    task.filepath = ydl.prepare_filename(info)
                except Exception:  # noqa: BLE001
                    task.filepath = ""

    def _hook(self, task: DownloadTask, data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            with self._lock:
                task.progress = (downloaded / total * 100.0) if total else 0.0
                speed = data.get("speed")
                task.speed = f"{speed / 1048576:.1f} MB/s" if speed else ""
                eta = data.get("eta")
                task.eta = f"{int(eta)}s" if eta else ""
        elif status == "finished":
            with self._lock:
                task.progress = 100.0
                task.speed = ""
                task.eta = ""
        self._notify()

    def _notify(self, force: bool = False) -> None:
        if not self._on_change:
            return
        now = time.time()
        with self._lock:
            if not force and now - self._last_notify < 0.4:
                return
            self._last_notify = now
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - la UI no debe romper la descarga
            pass
