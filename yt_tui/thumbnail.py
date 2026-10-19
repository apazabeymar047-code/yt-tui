"""Thumbnail fetch via httpx + chafa render with on-disk cache."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
import re
import shutil
import subprocess

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = pathlib.Path.home() / ".cache" / "yt-tui" / "thumbnails"
WIDTH_DEF = 62
CHAFA_TIMEOUT = 8.0
_HASH_LEN = 16


def is_available() -> bool:
    return shutil.which("chafa") is not None


def _cache_path(url: str) -> pathlib.Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:_HASH_LEN]
    return CACHE_DIR / f"{h}.jpg"


async def _download(url: str) -> pathlib.Path | None:
    cached = _cache_path(url)
    if cached.exists() and cached.stat().st_size > 0:
        return cached
    try:
        data = await _fetch(url)
        if data:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return cached
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("thumbnail download failed: %s", url, exc_info=True)
    return None


async def _fetch(url: str) -> bytes | None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def _clean_ansi(text: str) -> str:
    """Strip ANSI escape sequences that break Textual/Rich rendering."""
    if not text:
        return ""
    # cursor hide/show, save/restore, mode sequences
    text = text.replace("\x1b[?25l", "").replace("\x1b[?25h", "")
    text = re.sub(r"\x1b7|\x1b8", "", text)
    text = re.sub(r"\x1b\[\??[0-9;]*[hl]", "", text)
    # reverse video (ESC[7m) — not supported by Rich, causes visual garbage
    text = re.sub(r"\x1b\[(?:[0-9;]*;)?7m|\x1b\[7(?:;[0-9;]*)?m", "", text)
    # OSC (Operating System Command) sequences, e.g., ESC ] ... BEL or ESC \
    text = re.sub(r"\x1b].*?(\x07|\x1b\\)", "", text, flags=re.DOTALL)
    return text


def _run_chafa_sync(img: pathlib.Path, width: int, height: int) -> str:
    size = f"{max(1, min(width, 180))}x{height}"
    # High-density symbols (vhalf+quad+sextant) + perceptually accurate color space
    cargs = [
        "chafa",
        "-f", "symbols",
        "--symbols", "vhalf+quad+sextant",
        "-c", "full",
        "--color-space", "din99d",
        "--work", "9",
        "--margin-bottom", "0",
        "-s", size,
        str(img),
    ]
    try:
        cp = subprocess.run(
            cargs,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHAFA_TIMEOUT,
        )
        if cp.returncode != 0:
            # Fallback to basic symbols if advanced classes are unsupported
            cargs_fallback = [
                "chafa", "-f", "symbols", "-c", "full",
                "--margin-bottom", "0",
                "-s", size,
                str(img),
            ]
            cp = subprocess.run(
                cargs_fallback,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=CHAFA_TIMEOUT,
            )
    except subprocess.TimeoutExpired:
        logger.warning("chafa timed-out")
        return ""
    except FileNotFoundError:
        logger.warning("chafa binary not found on PATH")
        return ""
    except Exception as exc:
        logger.warning("chafa failed: %s", exc)
        return ""

    if cp.returncode != 0:
        logger.debug("chafa RC=%s: %s", cp.returncode, cp.stderr.strip() if cp.stderr else "")
        return ""

    return _clean_ansi(cp.stdout or "")


async def render(url: str, width: int = WIDTH_DEF) -> str:
    """Return ANSI-art thumbnail string ready for ``Text.from_ansi()``."""
    if not url or not is_available():
        logger.debug("thumbnail.render: no url or chafa not available")
        return ""
    try:
        img = await _download(url)
        logger.debug(f"thumbnail.render: downloaded img={img}")
        if not img or not img.exists():
            logger.debug("thumbnail.render: image does not exist")
            return ""
        height = max(3, round(width * 9 / 16 / 2))
        result = await asyncio.to_thread(_run_chafa_sync, img, width, height)
        logger.debug(f"thumbnail.render: result length={len(result)}")
        return result
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("thumbnail render failed: %s", url, exc_info=True)
        return ""
