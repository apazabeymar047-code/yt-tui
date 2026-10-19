"""Shared helpers: formatting, config, safe JSON I/O."""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_views(count: int | None) -> str:
    if count is None:
        return "N/A"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = Path.home() / ".yt-tui"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> list | dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        try:
            # Compatibilidad con archivos antiguos (codificación del sistema).
            raw = path.read_text()
        except FileNotFoundError:
            return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def save_json(path: Path, data: list | dict) -> None:
    ensure_data_dir()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    """Devuelve el diccionario de preferencias del usuario."""
    data = load_json(DATA_DIR / "settings.json")
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    """Guarda las preferencias del usuario."""
    save_json(DATA_DIR / "settings.json", data)


# ── History ───────────────────────────────────────────────────────────────────

class HistoryManager:
    @classmethod
    def _search_hist_file(cls) -> Path:
        return DATA_DIR / "search_history.json"

    @classmethod
    def _watch_hist_file(cls) -> Path:
        return DATA_DIR / "watch_history.json"

    @classmethod
    def get_search_history(cls) -> list[dict]:
        return load_json(cls._search_hist_file())

    @classmethod
    def add_search(cls, query: str) -> None:
        history = [h for h in cls.get_search_history() if h.get("query") != query]
        history.insert(0, {
            "query": query,
            "timestamp": _now(),
        })
        save_json(cls._search_hist_file(), history[:50])  # Keep last 50

    @classmethod
    def remove_search(cls, query: str) -> None:
        history = [h for h in cls.get_search_history() if h.get("query") != query]
        save_json(cls._search_hist_file(), history)

    @classmethod
    def clear_search(cls) -> None:
        save_json(cls._search_hist_file(), [])

    @classmethod
    def get_watch_history(cls) -> list[dict]:
        return load_json(cls._watch_hist_file())

    @classmethod
    def add_watch(cls, video_data: dict) -> None:
        history = [h for h in cls.get_watch_history() if h.get("id") != video_data.get("id")]
        entry = dict(video_data)
        entry["watched_at"] = _now()
        history.insert(0, entry)
        save_json(cls._watch_hist_file(), history[:100])  # Keep last 100

    @classmethod
    def remove_watch(cls, vid_id: str) -> None:
        history = [h for h in cls.get_watch_history() if h.get("id") != vid_id]
        save_json(cls._watch_hist_file(), history)

    @classmethod
    def clear_watch(cls) -> None:
        save_json(cls._watch_hist_file(), [])


# ── Biblioteca del usuario (Favoritos / Ver más tarde) ────────────────────────

class Library:
    """Listas persistentes de vídeos: favoritos y 'ver más tarde'."""

    _FILES = {
        "favorites": "favorites.json",
        "later": "watch_later.json",
    }
    _LIMITS = {
        "favorites": 500,
        "later": 500,
    }
    _LABELS = {
        "favorites": "Favoritos",
        "later": "Ver más tarde",
    }

    @classmethod
    def _file(cls, which: str) -> Path:
        return DATA_DIR / cls._FILES.get(which, "favorites.json")

    @classmethod
    def label(cls, which: str) -> str:
        return cls._LABELS.get(which, which)

    @classmethod
    def get(cls, which: str) -> list[dict]:
        data = load_json(cls._file(which))
        return data if isinstance(data, list) else []

    @classmethod
    def contains(cls, which: str, vid_id: str) -> bool:
        return any(v.get("id") == vid_id for v in cls.get(which))

    @classmethod
    def add(cls, which: str, item: dict) -> None:
        if not item.get("id"):
            return
        entries = [v for v in cls.get(which) if v.get("id") != item["id"]]
        entries.insert(0, dict(item))
        save_json(cls._file(which), entries[: cls._LIMITS.get(which, 500)])

    @classmethod
    def remove(cls, which: str, vid_id: str) -> None:
        entries = [v for v in cls.get(which) if v.get("id") != vid_id]
        save_json(cls._file(which), entries)

    @classmethod
    def toggle(cls, which: str, item: dict) -> bool:
        """Añade o quita *item* de la lista. Devuelve True si quedó añadido."""
        vid_id = item.get("id")
        if not vid_id:
            return False
        if cls.contains(which, vid_id):
            cls.remove(which, vid_id)
            return False
        cls.add(which, item)
        return True


# ── Cola de reproducción ──────────────────────────────────────────────────────

class QueueStore:
    """Cola de vídeos para reproducir en secuencia, persistente."""

    @classmethod
    def _file(cls) -> Path:
        return DATA_DIR / "queue.json"

    @classmethod
    def get(cls) -> list[dict]:
        data = load_json(cls._file())
        return data if isinstance(data, list) else []

    @classmethod
    def save(cls, items: list[dict]) -> None:
        save_json(cls._file(), items[:200])

    @classmethod
    def add(cls, item: dict) -> bool:
        """Añade al final. Devuelve False si ya estaba (por id)."""
        vid_id = item.get("id")
        if not vid_id:
            return False
        items = cls.get()
        if any(v.get("id") == vid_id for v in items):
            return False
        items.append(dict(item))
        cls.save(items)
        return True

    @classmethod
    def remove_at(cls, idx: int) -> bool:
        items = cls.get()
        if not (0 <= idx < len(items)):
            return False
        items.pop(idx)
        cls.save(items)
        return True

    @classmethod
    def remove_id(cls, vid_id: str) -> bool:
        """Quita de la cola la entrada con ese id. True si estaba."""
        items = cls.get()
        remaining = [v for v in items if v.get("id") != vid_id]
        if len(remaining) == len(items):
            return False
        cls.save(remaining)
        return True

    @classmethod
    def contains(cls, vid_id: str) -> bool:
        return any(v.get("id") == vid_id for v in cls.get())

    @classmethod
    def clear(cls) -> None:
        cls.save([])


# ── Portapapeles ──────────────────────────────────────────────────────────────

def copy_to_clipboard(text: str) -> bool:
    """Copia *text* al portapapeles. Devuelve True si lo consiguió.

    No lanza excepciones: si no hay herramienta disponible, devuelve False.
    """
    if not text:
        return False
    data = text.encode("utf-8")
    try:
        if sys.platform == "win32":
            try:
                subprocess.run(["clip"], input=data, check=True)
                return True
            except Exception:
                # Alternativa con PowerShell (mejor con acentos/emoji)
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                    input=text,
                    text=True,
                    check=True,
                )
                return True
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=data, check=True)
            return True
        subprocess.run(
            ["xclip", "-selection", "clipboard"], input=data, check=True
        )
        return True
    except Exception:
        return False
