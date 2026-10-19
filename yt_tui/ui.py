"""Textual TUI layer for YT-TUI."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from typing import ClassVar

from textual import events, on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import CommandPalette
from textual.containers import Horizontal, Vertical, Container
from textual.screen import ModalScreen, Screen
from textual.theme import ThemeProvider
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    OptionList,
    Select,
    Static,
)

import yt_tui.downloads as downloads
import yt_tui.player as player
import yt_tui.search as search
import yt_tui.thumbnail as thumbnail
from rich.align import Align
from rich.markup import escape
from rich.text import Text
from textual.command import CommandList, CommandPalette
from textual.notifications import Notification, Notify
from textual.widgets.option_list import Option
from yt_tui.utils import (
    copy_to_clipboard,
    fmt_duration,
    fmt_views,
    HistoryManager,
    Library,
    QueueStore,
    load_settings,
    save_settings,
)

logger = logging.getLogger(__name__)

LOGO = """\
 ██╗   ██╗████████╗      ██████╗██╗     ██╗
 ╚██╗ ██╔╝╚══██╔══╝     ██╔════╝██║     ██║
  ╚████╔╝    ██║   █████╗██║     ██║     ██║
   ╚██╔╝     ██║   ╚════╝██║     ██║     ██║
    ██║      ██║         ╚██████╗███████╗██║
    ╚═╝      ╚═╝          ╚═════╝╚══════╝╚═╝"""

HELP_TEXT = """\
[b]Referencia de teclas[/b]

  [yellow]F1[/yellow]          Mostrar / ocultar esta ayuda
  [yellow]/[/yellow]          Ir a la barra de búsqueda
  [yellow]↑ / ↓[/yellow]      Moverse entre resultados
  [yellow]Enter[/yellow]      Abrir el menú de acciones del vídeo
                 (reproducir, solo audio, descargar, favorito,
                  ver más tarde, cola, copiar URL, info)
  [yellow]F[/yellow]          Añadir / quitar de Favoritos (atajo directo)
  [yellow]L[/yellow]          Añadir / quitar de Ver más tarde (atajo directo)
  [yellow]M[/yellow]          Cargar más resultados
  [yellow]O[/yellow]          Ordenar resultados
  [yellow]Q[/yellow]          Añadir a la cola de reproducción (atajo directo)
  [yellow]Supr[/yellow]       Borrar la entrada seleccionada
  [yellow]R[/yellow]          Reanudar descargas (en la pestaña Descargas)
  [yellow]Ctrl+O[/yellow]     Abrir la carpeta de descargas
  [yellow]A[/yellow]          Activar / desactivar autoplay
  [yellow]S[/yellow]          Pestaña de búsqueda
  [yellow]H[/yellow]          Pestaña de historial
  [yellow]T[/yellow]          Abrir miniatura en HD
  [yellow]Ctrl+Q[/yellow]     Salir

[b]Durante la reproducción con mpv[/b]

  [yellow]Espacio[/yellow]    Pausar / reanudar
  [yellow]← / →[/yellow]      Retroceder / avanzar 5 s
  [yellow]↑ / ↓[/yellow]      Saltar 60 s adelante / atrás
  [yellow]9 / 0[/yellow]      Subir / bajar volumen
  [yellow]m[/yellow]          Silenciar
  [yellow]q[/yellow]          Detener y volver a YT-TUI
"""


class SafeInput(Input):
    """Buscador que descarta los restos de los avisos de ratón del terminal.

    En Windows, Textual pide al terminal que le informe de cada movimiento del
    ratón. Si un aviso llega cortado en dos trozos, Textual se rinde a los
    100 ms, tira la tecla ESC y escribe el resto como si lo hubiera tecleado
    el usuario (por ejemplo ``[<32;124;5M``). Esta clase reconoce esos
    fragmentos y los elimina antes de que ensucien la búsqueda.
    """

    # Caracteres que pueden aparecer en un aviso de ratón SGR.
    _JUNK_ALPHABET = frozenset("0123456789;-<>Mm")
    # Restos que pueden llegar después de un aviso cortado (sin `[` ni `<`).
    _TAIL_ALPHABET = frozenset("0123456789;-Mm")
    _JUNK_START = frozenset("[<")
    # Secuencia completa (o un trozo suficientemente claro) de ratón.
    _JUNK_FULL = re.compile(r"\[?<[\d;-]+[Mm]")
    # Si las teclas llegan más rápido que esto, no son de una persona.
    _BURST_GAP = 0.05
    # Tiempo de espera antes de decidir si lo acumulado era texto normal.
    _FLUSH_DELAY = 0.35
    # Margen extra para tragar trozos que llegan justo después.
    _COOLDOWN = 0.15
    _MAX_BUFFER = 200

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._junk_buffer = ""
        self._junk_confirmed = False
        self._junk_cooldown_until = 0.0
        self._last_junk_time = 0.0
        self._junk_timer = None

    # -- utilidades internas -------------------------------------------------

    def _cancel_timer(self) -> None:
        if self._junk_timer is not None:
            self._junk_timer.stop()
            self._junk_timer = None

    def _restart_timer(self) -> None:
        self._cancel_timer()
        self._junk_timer = self.set_timer(self._FLUSH_DELAY, self._resolve_junk)

    def _insert_text(self, text: str) -> None:
        if not text:
            return
        selection = self.selection
        if selection.is_empty:
            self.insert_text_at_cursor(text)
        else:
            self.replace(text, *selection)

    @classmethod
    def _looks_like_junk(cls, text: str) -> bool:
        """¿Esto ya no puede ser otra cosa que un aviso de ratón?"""
        if cls._JUNK_FULL.search(text):
            return True
        if text.startswith("[<"):
            return True
        return len(text) >= 2 and text[0] == "<" and text[1].isdigit()

    def _resolve_junk(self) -> None:
        """Decide qué hacer con lo acumulado: escribirlo o tirarlo."""
        self._cancel_timer()
        buffered, self._junk_buffer = self._junk_buffer, ""
        was_junk, self._junk_confirmed = self._junk_confirmed, False
        self._junk_timer = None
        if buffered and not was_junk and self.is_attached:
            self._insert_text(buffered)

    def _swallow(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    # -- manejo de teclas ----------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        char = event.character
        now = time.monotonic()

        if not char or len(char) != 1 or not event.is_printable:
            self._resolve_junk()
            await super()._on_key(event)
            return

        if self._junk_buffer:
            # ¿Sigue siendo el mismo chorro de caracteres "de máquina"?
            if char in self._JUNK_ALPHABET and (now - self._last_junk_time) <= self._BURST_GAP:
                self._swallow(event)
                self._junk_buffer += char
                self._last_junk_time = now
                if not self._junk_confirmed and self._looks_like_junk(self._junk_buffer):
                    self._junk_confirmed = True
                if self._junk_confirmed:
                    if self._junk_buffer.endswith(("M", "m")):
                        # Secuencia completa: no hace falta esperar restos.
                        self._junk_cooldown_until = 0.0
                    else:
                        self._junk_cooldown_until = now + self._COOLDOWN
                if len(self._junk_buffer) > self._MAX_BUFFER:
                    self._resolve_junk()
                else:
                    self._restart_timer()
                return
            # No encaja: lo anterior era texto normal.
            self._resolve_junk()

        # Restos que llegan un poco después de una secuencia ya identificada.
        if now <= self._junk_cooldown_until and char in self._TAIL_ALPHABET:
            self._swallow(event)
            self._junk_cooldown_until = now + self._COOLDOWN
            return

        if char in self._JUNK_START:
            # Posible inicio de un aviso de ratón: esperamos un poco.
            self._swallow(event)
            self._junk_buffer = char
            self._junk_confirmed = False
            self._last_junk_time = now
            self._restart_timer()
            return

        await super()._on_key(event)


class HelpScreen(ModalScreen):
    BINDINGS: ClassVar = [Binding("escape,?", "dismiss", "Cerrar")]

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help-box")

    def on_mount(self) -> None:
        self.query_one("#help-box").styles.padding = (2, 4)


class ActionsScreen(ModalScreen[str | None]):
    """Menú de acciones para un vídeo.

    Devuelve la acción que cierra el menú (``"play"``, ``"audio"``,
    ``"download"`` o ``"info"``), o ``None`` si se cancela. Las acciones que
    no cierran (favorito, ver más tarde, cola, copiar URL) se aplican al
    momento y el menú sigue abierto, actualizando sus textos.
    """

    BINDINGS: ClassVar = [
        Binding("v", "pick('play')", "Reproducir", show=False),
        Binding("a", "pick('audio')", "Solo audio", show=False),
        Binding("d", "pick('download')", "Descargar", show=False),
        Binding("f", "pick('favorite')", "Favorito", show=False),
        Binding("l", "pick('later')", "Ver más tarde", show=False),
        Binding("q", "pick('queue')", "A la cola", show=False),
        Binding("c", "pick('copy')", "Copiar URL", show=False),
        Binding("i", "pick('info')", "Info", show=False),
        Binding("escape", "cancel", "Cerrar", show=False),
    ]

    def __init__(
        self,
        item: dict,
        *,
        on_toggle: Callable[[str, object], None] | None = None,
    ) -> None:
        super().__init__()
        self._item = item
        self._on_toggle = on_toggle

    # ── Opciones ─────────────────────────────────────────────────────────────

    def _options(self) -> list[Option]:
        vid_id = str(self._item.get("id") or "")
        fav = bool(vid_id and Library.contains("favorites", vid_id))
        later = bool(vid_id and Library.contains("later", vid_id))
        in_queue = bool(vid_id and QueueStore.contains(vid_id))
        return [
            Option("🎬  Reproducir", id="play"),
            Option("🎵  Solo audio", id="audio"),
            Option("⬇️  Descargar", id="download"),
            Option(
                "⭐  Quitar de Favoritos" if fav else "⭐  Añadir a Favoritos",
                id="favorite",
            ),
            Option(
                "⏰  Quitar de Ver más tarde" if later else "⏰  Añadir a Ver más tarde",
                id="later",
            ),
            Option(
                "🎬  Quitar de la cola" if in_queue else "🎬  Añadir a la cola",
                id="queue",
            ),
            Option("🔗  Copiar URL", id="copy"),
            Option("ℹ️  Información", id="info"),
        ]

    def compose(self) -> ComposeResult:
        item = self._item
        meta = fmt_duration(item.get("duration"))
        views = fmt_views(item.get("views"))
        if views and views != "N/A":
            meta = f"{meta}  ·  {views}"
        with Vertical(id="actions-dialog"):
            yield Label("Acciones del vídeo", id="actions-title")
            yield Label(escape(str(item.get("title", "Desconocido"))), id="actions-video-title")
            yield Label(
                f"{escape(str(item.get('channel', 'Desconocido')))}  ·  {meta}",
                id="actions-video-meta",
            )
            yield OptionList(*self._options(), id="actions-list")
            yield Label("", id="actions-feedback")
            yield Label(
                "V=Reproducir · A=Solo audio · D=Descargar · F=favorito · "
                "L=ver más tarde · Q=cola · C=copiar URL · I=info",
                id="actions-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#actions-list", OptionList).focus()

    def _rebuild(self) -> None:
        option_list = self.query_one("#actions-list", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        option_list.add_options(self._options())
        if highlighted is not None and highlighted < len(option_list.options):
            option_list.highlighted = highlighted

    def _feedback(self, message: str) -> None:
        self.query_one("#actions-feedback", Label).update(message)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_pick(self, action: str) -> None:
        self._handle(action)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._handle(event.option.id)

    # ── Lógica de cada acción ────────────────────────────────────────────────

    def _notify_toggle(self, action: str, value: object) -> None:
        if self._on_toggle is not None:
            self._on_toggle(action, value)

    def _handle(self, action: str) -> None:
        vid_id = str(self._item.get("id") or "")

        if action in ("play", "audio", "download", "info"):
            self.dismiss(action)
            return

        if action == "favorite":
            added = Library.toggle("favorites", self._item)
            self._feedback("⭐ Añadido a Favoritos" if added else "Quitado de Favoritos")
            self._notify_toggle("favorite", added)
            self._rebuild()
        elif action == "later":
            added = Library.toggle("later", self._item)
            self._feedback(
                "⏰ Guardado en Ver más tarde" if added else "Quitado de Ver más tarde"
            )
            self._notify_toggle("later", added)
            self._rebuild()
        elif action == "queue":
            if not vid_id:
                self._feedback("Este vídeo no se puede añadir a la cola")
                return
            if QueueStore.contains(vid_id):
                QueueStore.remove_id(vid_id)
                self._feedback("Quitado de la cola")
                self._notify_toggle("queue", False)
            else:
                QueueStore.add(self._item)
                self._feedback("🎬 Añadido a la cola")
                self._notify_toggle("queue", True)
            self._rebuild()
        elif action == "copy":
            url = str(self._item.get("url") or "")
            ok = copy_to_clipboard(url) if url else False
            self._feedback(
                "🔗 URL copiada al portapapeles" if ok else "No se pudo copiar la URL"
            )
            self._notify_toggle("copy", ok)


class InfoScreen(ModalScreen[None]):
    """Ficha con los datos de un vídeo."""

    BINDINGS: ClassVar = [
        Binding("escape", "close", "Cerrar", show=False),
        Binding("c", "copy", "Copiar URL", show=False),
    ]

    def __init__(self, item: dict) -> None:
        super().__init__()
        self._item = item

    def compose(self) -> ComposeResult:
        item = self._item
        vid_id = str(item.get("id") or "")
        info = player.progress_info(vid_id) if vid_id else None
        if info:
            progress = f"{info['percent']}%  (minuto {int(info['pos'] // 60)})"
        else:
            progress = "No empezado"

        with Vertical(id="info-dialog-modal"):
            yield Label("Información del vídeo", id="info-modal-title")
            yield Label(escape(str(item.get("title", "Desconocido"))), id="info-modal-video")
            yield Label(
                f"[b]Canal:[/b] {escape(str(item.get('channel', 'Desconocido')))}"
            )
            yield Label(f"[b]Duración:[/b] {fmt_duration(item.get('duration'))}")
            yield Label(f"[b]Vistas:[/b] {fmt_views(item.get('views'))}")
            yield Label(f"[b]Visto:[/b] {progress}")
            yield Label(f"[b]ID:[/b] {vid_id or '—'}")
            yield Label(f"[b]URL:[/b] {escape(str(item.get('url') or '—'))}")
            with Horizontal(id="info-modal-buttons"):
                yield Button("🔗  Copiar URL", id="info-copy")
                yield Button("Cerrar", variant="primary", id="info-close")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_copy(self) -> None:
        url = str(self._item.get("url") or "")
        self.query_one("#info-modal-title", Label).update(
            "Información del vídeo  ·  🔗 URL copiada"
            if copy_to_clipboard(url)
            else "Información del vídeo  ·  no se pudo copiar"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "info-copy":
            self.action_copy()
        else:
            self.dismiss(None)


class PreferencesScreen(ModalScreen[dict | None]):
    """Ventana de preferencias de reproducción."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancelar")]

    def compose(self) -> ComposeResult:
        cfg = load_settings()

        quality_options = [
            ("Mejor disponible", "best"),
            ("1080p", "1080p"),
            ("720p", "720p"),
            ("480p", "480p"),
        ]
        quality = str(cfg.get("quality", "best"))
        if quality not in {v for _, v in quality_options}:
            quality = "best"

        speed_options = [
            ("Normal (1x)", "1.0"),
            ("1.25x", "1.25"),
            ("1.5x", "1.5"),
            ("2x", "2.0"),
        ]
        speed = str(cfg.get("speed", "1.0"))
        if speed not in {v for _, v in speed_options}:
            speed = "1.0"

        audio_options = [
            ("Original (automático)", ""),
            ("Español", "es"),
            ("Inglés", "en"),
            ("Español, Inglés", "es,en"),
        ]
        audio_lang = str(cfg.get("audio_lang", ""))
        if audio_lang not in {v for _, v in audio_options}:
            audio_lang = ""

        sub_options = [
            ("Sin preferencia", ""),
            ("Español", "es"),
            ("Inglés", "en"),
            ("Español, Inglés", "es,en"),
        ]
        sub_lang = str(cfg.get("sub_lang", "es,en"))
        if sub_lang not in {v for _, v in sub_options}:
            sub_lang = "es,en"

        notify_options = [
            ("3 segundos", "3"),
            ("5 segundos", "5"),
            ("8 segundos", "8"),
        ]
        notify_timeout = str(cfg.get("notify_timeout", "5"))
        if notify_timeout not in {v for _, v in notify_options}:
            notify_timeout = "5"

        thumb_options = [
            ("Pequeña (más sitio para la lista)", "small"),
            ("Mediana (actual)", "medium"),
            ("Grande", "large"),
            ("Muy grande (ventana ancha)", "xlarge"),
        ]
        thumb_size = str(cfg.get("thumb_size", "medium"))
        if thumb_size not in {v for _, v in thumb_options}:
            thumb_size = "medium"

        volume_options = [(f"{v} %", str(v)) for v in range(0, 101, 5)]
        try:
            volume = int(cfg.get("volume", 100))
        except (TypeError, ValueError):
            volume = 100
        volume = min(100, max(0, round(volume / 5) * 5))
        volume_value = str(volume)

        with Vertical(id="prefs-dialog"):
            yield Label("Preferencias de reproducción", id="prefs-title")

            yield Label("Calidad de vídeo", classes="pref-label")
            yield Select(
                quality_options,
                value=quality,
                allow_blank=False,
                id="pref-quality",
            )

            yield Label("Volumen al reproducir", classes="pref-label")
            yield Select(
                volume_options,
                value=volume_value,
                allow_blank=False,
                id="pref-volume",
            )

            yield Label("Velocidad", classes="pref-label")
            yield Select(
                speed_options,
                value=speed,
                allow_blank=False,
                id="pref-speed",
            )

            yield Label("Idioma del audio", classes="pref-label")
            yield Select(
                audio_options,
                value=audio_lang,
                allow_blank=False,
                id="pref-alang",
            )

            yield Label("Idioma de los subtítulos", classes="pref-label")
            yield Select(
                sub_options,
                value=sub_lang,
                allow_blank=False,
                id="pref-slang",
            )

            yield Checkbox(
                "Activar subtítulos",
                value=bool(cfg.get("subtitles", False)),
                id="pref-subs",
            )
            yield Checkbox(
                "Reanudar donde quedé",
                value=bool(cfg.get("resume", True)),
                id="pref-resume",
            )

            yield Checkbox(
                "Avisos en pantalla",
                value=bool(cfg.get("notify_enabled", True)),
                id="pref-notify",
            )
            yield Label("Duración de los avisos", classes="pref-label")
            yield Select(
                notify_options,
                value=notify_timeout,
                allow_blank=False,
                id="pref-notify-timeout",
            )

            yield Label("Tamaño de la miniatura", classes="pref-label")
            yield Select(
                thumb_options,
                value=thumb_size,
                allow_blank=False,
                id="pref-thumb",
            )

            with Horizontal(id="prefs-buttons"):
                yield Button("Guardar", variant="primary", id="pref-save")
                yield Button("Cancelar", id="pref-cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pref-save":
            self.dismiss(
                {
                    "quality": self.query_one("#pref-quality", Select).value,
                    "volume": int(self.query_one("#pref-volume", Select).value),
                    "speed": self.query_one("#pref-speed", Select).value,
                    "audio_lang": self.query_one("#pref-alang", Select).value,
                    "sub_lang": self.query_one("#pref-slang", Select).value,
                    "subtitles": self.query_one("#pref-subs", Checkbox).value,
                    "resume": self.query_one("#pref-resume", Checkbox).value,
                    "notify_enabled": self.query_one("#pref-notify", Checkbox).value,
                    "notify_timeout": int(
                        self.query_one("#pref-notify-timeout", Select).value
                    ),
                    "thumb_size": self.query_one("#pref-thumb", Select).value,
                }
            )
        else:
            self.dismiss(None)


class SortScreen(ModalScreen[tuple[str, bool] | None]):
    """Ventana para elegir cómo ordenar los resultados."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancelar")]

    OPCIONES: ClassVar = [
        ("Relevancia (original)", "relevance"),
        ("Duración", "duration"),
        ("Más vistos", "views"),
        ("Título (A-Z)", "title"),
    ]

    def __init__(
        self, current_key: str = "relevance", current_reverse: bool = True
    ) -> None:
        super().__init__()
        self._key = current_key
        self._reverse = current_reverse

    def compose(self) -> ComposeResult:
        valid = {value for _, value in self.OPCIONES}
        value = self._key if self._key in valid else "relevance"
        with Vertical(id="sort-dialog"):
            yield Label("Ordenar resultados", id="sort-title")
            yield Label("Criterio", classes="pref-label")
            yield Select(
                self.OPCIONES,
                value=value,
                allow_blank=False,
                id="sort-key",
            )
            yield Label("Dirección", classes="pref-label")
            yield Select(
                [
                    ("Descendente (mayor primero)", "desc"),
                    ("Ascendente (menor primero)", "asc"),
                ],
                value="desc" if self._reverse else "asc",
                allow_blank=False,
                id="sort-dir",
            )
            with Horizontal(id="sort-buttons"):
                yield Button("Aplicar", variant="primary", id="sort-apply")
                yield Button("Cancelar", id="sort-cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sort-apply":
            key = str(self.query_one("#sort-key", Select).value)
            direction = str(self.query_one("#sort-dir", Select).value)
            self.dismiss((key, direction == "desc"))
        else:
            self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Confirmación simple de Sí/No."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label("¿Seguro?", id="confirm-title")
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Sí, borrar", variant="error", id="confirm-yes")
                yield Button("Cancelar", variant="primary", id="confirm-no")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class DownloadQualityScreen(ModalScreen[str | None]):
    """Ventana para elegir la calidad de una descarga."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        cfg = load_settings()
        last = str(cfg.get("download_quality", "best"))
        valid = {value for _, value in downloads.QUALITY_OPTIONS}
        if last not in valid:
            last = "best"

        with Vertical(id="download-dialog"):
            yield Label("Calidad de descarga", id="download-title")
            yield Label(self._title, id="download-video-title")
            yield Select(
                downloads.QUALITY_OPTIONS,
                value=last,
                allow_blank=False,
                id="download-quality",
            )
            with Horizontal(id="download-buttons"):
                yield Button("⬇️  Descargar", variant="primary", id="download-go")
                yield Button("Cancelar", id="download-cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "download-go":
            self.dismiss(str(self.query_one("#download-quality", Select).value))
        else:
            self.dismiss(None)


class PaletaComandos(CommandPalette):
    """Paleta de comandos de Textual con los mensajes en español."""

    def _start_no_matches_countdown(self, search_value: str) -> None:
        self._stop_no_matches_countdown()

        def _mostrar_sin_resultados() -> None:
            if search_value:
                command_list = self.query_one(CommandList)
                command_list.add_option(
                    Option(
                        Align.center(Text("Sin resultados", style="not bold")),
                        disabled=True,
                        id=self._NO_MATCHES,
                    )
                )
                self._list_visible = True
            else:
                self._list_visible = False

        self._no_matches_timer = self.set_timer(
            self._NO_MATCHES_COUNTDOWN, _mostrar_sin_resultados
        )


class YtApp(App):
    TITLE = "YT-TUI"
    SUB_TITLE = "Cliente de YouTube para la terminal"

    CSS = """
    $accent-primary: #FF2D2D;
    $accent-secondary: #f5c2e7;
    $accent-green: #a6e3a1;
    $bg-main: #1e1e2e;
    $bg-sidebar: #181825;
    $bg-surface: #232336;
    $fg-main: #cdd6f4;
    $fg-muted: #7f849c;
    $border-dim: #313244;

    Screen {
        background: $bg-main;
        color: $fg-main;
    }

    /* Avisos flotantes (toasts) */
    ToastRack {
        dock: bottom;
        align-horizontal: right;
        margin-bottom: 1;
    }

    Toast {
        background: $bg-sidebar;
        color: $fg-main;
    }

    Toast.-information {
        border-left: thick $accent-primary;
    }

    Toast.-warning {
        border-left: thick #f9e2af;
    }

    Toast.-error {
        border-left: thick #f38ba8;
    }

    #sidebar {
        width: 25;
        background: $bg-sidebar;
        border-right: solid #313244;
        dock: left;
    }

    #sidebar-logo {
        height: 6;
        content-align: center middle;
        color: $accent-primary;
        text-style: bold;
        border-bottom: solid $border-dim;
    }

    #nav-list {
        background: transparent;
        border: none;
    }

    #nav-list ListItem {
        padding: 1 2;
        border-left: solid $bg-sidebar;
    }

    #nav-list ListItem:hover {
        background: $border-dim;
        border-left: solid $accent-primary;
    }

    #nav-list ListItem.--highlight {
        background: $accent-primary;
        color: $bg-main;
        text-style: bold;
        border-left: solid $accent-primary;
    }

    #content {
        height: 1fr;
    }

    #search, #history {
        height: 1fr;
    }

    #search {
        border: solid $border-dim;
        height: 1fr;
    }

    #history {
        border: solid $border-dim;
        height: 1fr;
    }

    #favorites, #later, #queue, #downloads {
        border: solid $border-dim;
        height: 1fr;
    }

    .panel-hint {
        color: $fg-muted;
        padding: 0 1;
        height: 1;
    }

    #search-header {
        height: 3;
        padding: 0 1;
        margin-bottom: 1;
    }

    #search-bar {
        border: solid $border-dim;
    }

    #search-bar:focus {
        border: solid $accent-primary;
    }

    #results-container {
        height: 1fr;
    }

    #results {
        border: none;
        background: transparent;
    }

    #results > .datatable--header {
        background: $bg-surface;
        color: $accent-primary;
        text-style: bold;
    }

    #results > .datatable--line {
        padding: 0 1;
    }

    #info-panel {
        width: 66;
        border-left: solid $border-dim;
        padding: 0 1;
        background: $bg-sidebar;
    }

    #thumb {
        height: 19;
        width: 100%;
        margin-bottom: 1;
        border: solid $accent-primary;
        content-align: center middle;
        overflow: hidden;
    }

    /* Tamaños de la miniatura (se eligen en Preferencias) */
    #info-panel.thumb-size-small { width: 55; }
    #info-panel.thumb-size-medium { width: 66; }
    #info-panel.thumb-size-large { width: 80; }
    #info-panel.thumb-size-xlarge { width: 96; }

    #thumb.thumb-size-small { height: 16; }
    #thumb.thumb-size-medium { height: 19; }
    #thumb.thumb-size-large { height: 23; }
    #thumb.thumb-size-xlarge { height: 28; }

    #info-title {
        text-style: bold;
        color: $accent-secondary;
        margin-bottom: 1;
        border-bottom: solid $border-dim;
        padding-bottom: 1;
    }

    #status {
        dock: bottom;
        height: 1;
        background: $accent-primary;
        color: $bg-main;
        padding: 0 1;
        text-style: bold;
    }

    #loading-overlay {
        width: 100%;
        height: 100%;
        content-align: center middle;
        background: rgba(30, 30, 46, 0.8);
        display: none;
    }

    #loading-overlay.visible {
        display: block;
    }

    LoadingIndicator {
        color: $accent-primary;
    }

    HelpScreen > Static {
        background: $bg-sidebar;
        border: double $accent-primary;
        width: 60;
        height: auto;
        padding: 1 2;
    }

    #actions-dialog, #info-dialog-modal {
        background: $bg-sidebar;
        border: thick $accent-primary;
        width: 64;
        height: auto;
        padding: 1 2;
    }

    #actions-title, #info-modal-title {
        text-style: bold;
        color: $accent-secondary;
        content-align: center middle;
        width: 100%;
        padding: 1 0;
    }

    #actions-video-title, #info-modal-video {
        text-style: bold;
        color: $fg-main;
        content-align: center middle;
        width: 100%;
    }

    #actions-video-meta {
        color: $fg-muted;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }

    #actions-list {
        height: auto;
        max-height: 12;
        background: transparent;
        border: none;
    }

    #actions-feedback {
        color: green;
        text-style: bold;
        content-align: center middle;
        width: 100%;
        height: 1;
        margin-top: 1;
    }

    #actions-hint {
        color: $fg-muted;
        content-align: center middle;
        width: 100%;
        margin-top: 1;
    }

    #info-dialog-modal Label {
        width: 100%;
        height: auto;
    }

    #info-modal-buttons {
        align: center middle;
        height: auto;
        padding: 1 0;
    }

    #info-modal-buttons Button {
        margin: 0 1;
    }

    #prefs-dialog, #sort-dialog, #confirm-dialog, #download-dialog {
        background: $bg-sidebar;
        border: thick $accent-primary;
        width: 60;
        height: auto;
        padding: 1 2;
    }

    #prefs-dialog {
        max-height: 90%;
        overflow-y: auto;
    }

    #prefs-title, #sort-title, #confirm-title, #download-title {
        text-style: bold;
        color: $accent-secondary;
        content-align: center middle;
        width: 100%;
        padding: 1 0;
    }

    #download-video-title {
        color: $fg-muted;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }

    .pref-label {
        color: $fg-main;
        text-style: bold;
        margin-top: 1;
    }

    #prefs-dialog Select {
        width: 100%;
    }

    #sort-dialog Select {
        width: 100%;
    }

    #download-dialog Select {
        width: 100%;
    }

    #confirm-message {
        color: $fg-main;
        content-align: center middle;
        width: 100%;
        margin-top: 1;
    }

    #prefs-dialog Checkbox {
        width: 100%;
        background: transparent;
        margin-top: 1;
    }

    #prefs-buttons {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    #prefs-buttons Button {
        margin: 0 1;
    }

    #sort-buttons, #confirm-buttons, #download-buttons {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    #sort-buttons Button, #confirm-buttons Button, #download-buttons Button {
        margin: 0 1;
    }

    .section-title {
        text-style: bold;
        color: $accent-primary;
        padding: 1 2;
        background: $bg-surface;
        width: 100%;
    }

    #history-table {
        border: none;
        background: transparent;
    }

    #favorites-table, #later-table, #watch-table, #queue-table, #downloads-table {
        border: none;
        background: transparent;
    }

    #history-table > .datatable--header,
    #favorites-table > .datatable--header,
    #later-table > .datatable--header,
    #watch-table > .datatable--header,
    #queue-table > .datatable--header,
    #downloads-table > .datatable--header {
        background: $bg-surface;
        color: $accent-primary;
        text-style: bold;
    }

    #history-table, #watch-table {
        height: 1fr;
    }

    /* ── Scrollbar ──────────────────────────────────────────── */
    * > .scrollbar {
        color: $accent-primary;
    }

    * > .scrollbar-track {
        color: $border-dim;
    }
    """

    BINDINGS: ClassVar = [
        # Navegación con flechas (← / → saltan entre zonas; ↓ baja de la
        # barra de búsqueda a los resultados). Ver check_action().
        Binding("left", "focus_sidebar", "Menú", show=False, priority=True),
        Binding("right", "focus_content", "Panel", show=False, priority=True),
        Binding("down", "focus_results", "Bajar", show=False),
        Binding("/", "focus_search", "Buscar", show=True),
        Binding("a", "toggle_autoplay", "Autoplay", show=True),
        Binding("f", "toggle_favorite", "Favorito", show=True),
        Binding("l", "toggle_later", "Ver más tarde", show=False),
        Binding("m", "load_more", "Más resultados", show=False),
        Binding("o", "sort_results", "Ordenar", show=False),
        Binding("q", "add_to_queue", "A la cola", show=False),
        Binding("r", "resume_downloads", "Reanudar", show=False),
        Binding("ctrl+o", "open_downloads", "Abrir descargas", show=False),
        Binding("delete", "delete_entry", "Borrar", show=False),
        Binding("h", "switch_view('history')", "Historial", show=True),
        Binding("s", "switch_view('search')", "Búsqueda", show=True),
        Binding("t", "open_thumb", "Miniatura HD", show=True),
        Binding("p", "preferences", "Preferencias", show=True),
        Binding("ctrl+q", "quit", "Salir", show=True),
        Binding("f1", "help", "Ayuda", show=True),
    ]

    # Ancho de la miniatura (en columnas) para cada tamaño de Preferencias.
    THUMB_SIZES: ClassVar[dict[str, int]] = {
        "small": 50,
        "medium": 61,
        "large": 75,
        "xlarge": 91,
    }

    def __init__(self, incognito: bool = False) -> None:
        super().__init__()
        self._incognito = incognito
        self._thumb_size: str = "medium"
        self._thumb_width: int = self.THUMB_SIZES["medium"]
        self._results: list[dict] = []
        self._current_thumb_task: asyncio.Task | None = None
        self._autoplay: bool = False
        self._is_playlist_mode: bool = False
        self._audio_only_pref: bool = False
        self._last_mode: str | None = None
        self._search_query: str = ""
        self._search_count: int = 15
        self._queue: list[dict] = QueueStore.get()
        self._download_status: dict[str, str] = {}
        self._playing_notification: Notification | None = None
        self._downloads = downloads.DownloadManager(
            on_change=self._downloads_changed
        )
        cfg = load_settings()
        self._sort_key: str = str(cfg.get("sort_key", "relevance"))
        self._sort_reverse: bool = bool(cfg.get("sort_reverse", True))

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("YT-TUI INCOG" if self._incognito else "YT-TUI", id="sidebar-logo")
                with ListView(id="nav-list"):
                    yield ListItem(Label("Buscar"), id="nav-search")
                    yield ListItem(Label("⭐ Favoritos"), id="nav-favorites")
                    yield ListItem(Label("⏰ Ver más tarde"), id="nav-later")
                    yield ListItem(Label("📋 Cola"), id="nav-queue")
                    yield ListItem(Label("⬇️ Descargas"), id="nav-downloads")
                    yield ListItem(Label("Historial"), id="nav-history")

            with Vertical(id="content"):
                with ContentSwitcher(initial="search"):
                    with Vertical(id="search"):
                        yield SafeInput(placeholder="Buscar en YouTube…", id="search-bar")
                        with Horizontal(id="results-container"):
                            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
                            with Vertical(id="info-panel"):
                                yield Static("Sin vista previa", id="thumb")
                                yield Label("Selecciona un vídeo", id="info-title")
                                yield Label("", id="info-meta")
                        with Container(id="loading-overlay"):
                            yield LoadingIndicator()

                    with Vertical(id="favorites"):
                        yield Label("⭐ Favoritos", classes="section-title")
                        yield DataTable(id="favorites-table", cursor_type="row", zebra_stripes=True)

                    with Vertical(id="later"):
                        yield Label("⏰ Ver más tarde", classes="section-title")
                        yield DataTable(id="later-table", cursor_type="row", zebra_stripes=True)

                    with Vertical(id="queue"):
                        yield Label("📋 Cola de reproducción", classes="section-title")
                        yield DataTable(id="queue-table", cursor_type="row", zebra_stripes=True)
                        yield Label(
                            "Enter = menú de acciones · Q = añadir · Supr = quitar",
                            classes="panel-hint",
                        )

                    with Vertical(id="downloads"):
                        yield Label("⬇️ Descargas", classes="section-title")
                        yield DataTable(id="downloads-table", cursor_type="row", zebra_stripes=True)
                        yield Label(
                            "R = reanudar · Ctrl+O = abrir carpeta · Supr = quitar de la lista",
                            classes="panel-hint",
                        )

                    with Vertical(id="history"):
                        yield Label("Historial de búsquedas", classes="section-title")
                        yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
                        yield Label("Vídeos vistos", classes="section-title")
                        yield DataTable(id="watch-table", cursor_type="row", zebra_stripes=True)

        yield Static("Listo", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_thumb_size()
        self.query_one("#results", DataTable).add_columns(
            "Título", "Canal", "Duración", "Vistas"
        )

        for table_id in ("#favorites-table", "#later-table"):
            self.query_one(table_id, DataTable).add_columns(
                "Título", "Canal", "Duración", "Vistas"
            )

        self.query_one("#history-table", DataTable).add_columns("Búsqueda", "Fecha")
        self.query_one("#watch-table", DataTable).add_columns(
            "Título", "Canal", "Visto"
        )
        self.query_one("#queue-table", DataTable).add_columns(
            "Título", "Canal", "Duración"
        )
        self.query_one("#downloads-table", DataTable).add_columns(
            "Título", "Calidad", "Estado", "Progreso"
        )

        self._refresh_history()
        self._refresh_library("favorites")
        self._refresh_library("later")
        self._refresh_queue()
        self._refresh_downloads()

        # Reanuda descargas que quedaron a medias al cerrar la app.
        self._downloads.start()

        # Limpia la vieja opción "No volver a preguntar".
        cfg = load_settings()
        if "skip_choice" in cfg or "preferred_mode" in cfg:
            cfg.pop("skip_choice", None)
            cfg.pop("preferred_mode", None)
            save_settings(cfg)

        self.query_one("#search-bar", Input).focus()

    def _apply_thumb_size(self) -> None:
        """Aplica el tamaño de miniatura guardado en Preferencias."""
        cfg = load_settings()
        key = str(cfg.get("thumb_size", "medium"))
        if key not in self.THUMB_SIZES:
            key = "medium"
        self._thumb_size = key
        self._thumb_width = self.THUMB_SIZES[key]
        for widget_id in ("#info-panel", "#thumb"):
            widget = self.query_one(widget_id)
            for name in self.THUMB_SIZES:
                widget.remove_class(f"thumb-size-{name}")
            widget.add_class(f"thumb-size-{key}")

    def _refresh_thumb(self) -> None:
        """Vuelve a generar la miniatura del vídeo resaltado."""
        table = self.query_one("#results", DataTable)
        idx = table.cursor_row
        if not (0 <= idx < len(self._results)):
            return
        self.query_one("#thumb", Static).update("")
        item = self._results[idx]
        if item.get("thumbnail"):
            self._fetch_thumb(str(item["thumbnail"]))

    def _refresh_history(self) -> None:
        hist_table = self.query_one("#history-table", DataTable)
        hist_table.clear()
        watch_table = self.query_one("#watch-table", DataTable)
        watch_table.clear()
        if self._incognito:
            return
        for h in HistoryManager.get_search_history():
            hist_table.add_row(str(h.get("query", "")), str(h.get("timestamp", "")))
        for h in HistoryManager.get_watch_history():
            watch_table.add_row(
                str(h.get("title", "Desconocido"))[:60],
                str(h.get("channel", "Desconocido"))[:30],
                str(h.get("watched_at", "")),
            )

    def _refresh_library(self, which: str) -> None:
        table_id = "#favorites-table" if which == "favorites" else "#later-table"
        table = self.query_one(table_id, DataTable)
        table.clear()
        for item in Library.get(which):
            table.add_row(
                str(item.get("title", "Desconocido"))[:60],
                str(item.get("channel", "Desconocido"))[:30],
                fmt_duration(item.get("duration")),
                fmt_views(item.get("views")),
            )

    def _refresh_queue(self) -> None:
        self._queue = QueueStore.get()
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for item in self._queue:
            table.add_row(
                str(item.get("title", "Desconocido"))[:60],
                str(item.get("channel", "Desconocido"))[:30],
                fmt_duration(item.get("duration")),
            )

    def _refresh_downloads(self) -> None:
        table = self.query_one("#downloads-table", DataTable)
        table.clear()
        previous = getattr(self, "_download_status", {})
        current: dict[str, str] = {}
        for task in self._downloads.tasks():
            progress = f"{task.progress:.0f}%"
            if task.status == "downloading":
                if task.speed:
                    progress += f"  {task.speed}"
                if task.eta:
                    progress += f"  ETA {task.eta}"
            elif task.status == "error" and task.error:
                progress = task.error[:40]
            table.add_row(
                str(task.title)[:50],
                downloads.QUALITY_LABELS.get(task.quality, task.quality),
                downloads.STATUS_LABELS.get(task.status, task.status),
                progress,
            )

            # Aviso al terminar o fallar una descarga.
            old = previous.get(task.id)
            current[task.id] = task.status
            if old is None or old == task.status:
                continue
            if task.status == "completed":
                self._notify(f"✅ Descargado: {str(task.title)[:45]}")
            elif task.status == "error":
                self._notify(
                    f"❌ Error al descargar: {str(task.title)[:35]}",
                    "error",
                )
        self._download_status = current

    def _downloads_changed(self) -> None:
        """Callback del DownloadManager (puede venir de otro hilo)."""
        try:
            self.call_from_thread(self._refresh_downloads)
        except RuntimeError:
            # Ya estamos en el hilo de la interfaz.
            self._refresh_downloads()

    # ── Search ────────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#search-bar")
    def handle_search(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self._search_query = query
        self._search_count = 15
        if not self._incognito:
            HistoryManager.add_search(query)
        self._refresh_history()
        self._run_search()

    def _run_search(self) -> None:
        query = self._search_query
        if not query:
            self._set_status("Escribe algo para buscar")
            return
        self.query_one("#loading-overlay").add_class("visible")
        self._set_status(f"Buscando: {query}… ({self._search_count} resultados)")
        self._do_search(query, self._search_count)

    @work(thread=True)
    def _do_search(self, query: str, max_results: int) -> None:
        try:
            results, is_playlist = search.search_youtube(query, max_results=max_results)
        except Exception as exc:
            self.call_from_thread(self._handle_search_error, exc)
            return
        self.call_from_thread(self._populate_results, results, is_playlist)

    def _handle_search_error(self, exc: Exception) -> None:
        self.query_one("#loading-overlay").remove_class("visible")
        message = f"Error de búsqueda: {exc}"
        self._set_status(message)
        self._notify(f"❌ {message}", "error")

    def _apply_sort(self, results: list[dict]) -> list[dict]:
        key = self._sort_key
        reverse = self._sort_reverse
        if key == "duration":
            return sorted(results, key=lambda r: r.get("duration") or -1, reverse=reverse)
        if key == "views":
            return sorted(results, key=lambda r: r.get("views") or -1, reverse=reverse)
        if key == "title":
            return sorted(
                results,
                key=lambda r: str(r.get("title") or "").lower(),
                reverse=reverse,
            )
        return results

    def _sort_name(self) -> str:
        names = {
            "relevance": "relevancia",
            "duration": "duración",
            "views": "más vistos",
            "title": "título",
        }
        return names.get(self._sort_key, "relevancia")

    def _populate_results(self, results: list[dict], is_playlist: bool) -> None:
        self.query_one("#loading-overlay").remove_class("visible")
        self._results = self._apply_sort(results)
        self._is_playlist_mode = is_playlist
        if not self._results:
            self.query_one("#results", DataTable).clear()
            self._set_status("No se encontraron resultados.")
            return

        self._redraw_results()
        if is_playlist and not self._autoplay:
            self._autoplay = True
            self._set_status("Lista de reproducción cargada — Autoplay ACTIVADO")
        table = self.query_one("#results", DataTable)
        table.focus()
        table.move_cursor(row=0)

    def _redraw_results(self) -> None:
        table = self.query_one("#results", DataTable)
        table.clear()
        for r in self._results:
            table.add_row(
                str(r.get("title", "Desconocido"))[:60],
                str(r.get("channel", "Desconocido"))[:30],
                fmt_duration(r.get("duration")),
                fmt_views(r.get("views")),
            )
        self._set_status(f"{len(self._results)} resultados · orden: {self._sort_name()}")

    # ── Info panel + thumbnail ────────────────────────────────────────────────

    @on(DataTable.RowHighlighted, "#results")
    def handle_highlight(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if idx < 0 or idx >= len(self._results):
            return
        r = self._results[idx]
        title = str(r.get("title", "Desconocido"))
        channel = str(r.get("channel", "Desconocido"))
        marks = ""
        if r.get("id"):
            if Library.contains("favorites", r["id"]):
                marks += "  ⭐"
            if Library.contains("later", r["id"]):
                marks += "  ⏰"
            info = player.progress_info(str(r["id"]))
            if info and info["percent"] > 0:
                marks += f"  [green]▸ {info['percent']}%[/green]"
        self.query_one("#info-title", Label).update(escape(title) + marks)
        self.query_one("#info-meta", Label).update(
            f"[cyan]{escape(channel)}[/cyan]  "
            f"[yellow]{fmt_duration(r.get('duration'))}[/yellow]  "
            f"{fmt_views(r.get('views'))} vistas"
        )
        self.query_one("#thumb", Static).update("")
        logger.debug(f"handle_highlight: thumbnail URL: {r.get('thumbnail')}")
        if r.get("thumbnail"):
            self._fetch_thumb(r["thumbnail"])

    @work(exclusive=True)
    async def _fetch_thumb(self, url: str) -> None:
        from rich.text import Text
        from textual.content import Content
        thumb_widget = self.query_one("#thumb", Static)
        thumb_widget.update(Text("Cargando miniatura...", style="italic cyan"))
        logger.debug(f"_fetch_thumb: url={url}")

        rendered = await thumbnail.render(url, width=self._thumb_width)
        logger.debug(f"_fetch_thumb: rendered length={len(rendered)}")
        if rendered:
            thumb_widget.update(Content.from_rich_text(Text.from_ansi(rendered)))
        else:
            thumb_widget.update(Text("Vista previa no disponible", style="red"))

    # ── Playback ──────────────────────────────────────────────────────────────

    @on(DataTable.RowSelected, "#results")
    def handle_select(self, event: DataTable.RowSelected) -> None:
        self._open_actions(event.cursor_row)

    @on(DataTable.RowSelected, "#favorites-table")
    @on(DataTable.RowSelected, "#later-table")
    def handle_library_select(self, event: DataTable.RowSelected) -> None:
        self._open_actions(event.cursor_row)

    @on(DataTable.RowSelected, "#watch-table")
    def handle_watch_select(self, event: DataTable.RowSelected) -> None:
        rows = HistoryManager.get_watch_history()
        self._open_actions(event.cursor_row, rows)

    @on(DataTable.RowSelected, "#queue-table")
    def handle_queue_select(self, event: DataTable.RowSelected) -> None:
        self._open_actions(event.cursor_row, list(self._queue), continuous=True)

    def _view_items(self) -> list[dict]:
        """Vídeos de la vista activa."""
        view = self.query_one(ContentSwitcher).current
        if view == "favorites":
            return Library.get("favorites")
        if view == "later":
            return Library.get("later")
        if view == "queue":
            return list(self._queue)
        return self._results

    def _active_table(self) -> DataTable:
        view = self.query_one(ContentSwitcher).current
        if view == "favorites":
            return self.query_one("#favorites-table", DataTable)
        if view == "later":
            return self.query_one("#later-table", DataTable)
        if view == "queue":
            return self.query_one("#queue-table", DataTable)
        if view == "downloads":
            return self.query_one("#downloads-table", DataTable)
        if view == "history":
            return self.query_one("#history-table", DataTable)
        return self.query_one("#results", DataTable)

    def _open_actions(
        self,
        idx: int,
        items: list[dict] | None = None,
        continuous: bool = False,
    ) -> None:
        """Abre el menú de acciones para el vídeo *idx* de *items*."""
        items = items if items is not None else self._view_items()
        if not (0 <= idx < len(items)):
            return

        r = items[idx]

        def handle_toggle(action: str, value: object) -> None:
            if action == "favorite":
                self._refresh_library("favorites")
                message = "⭐ Añadido a Favoritos" if value else "Quitado de Favoritos"
                self._set_status(message)
                self._notify(message)
            elif action == "later":
                self._refresh_library("later")
                message = (
                    "⏰ Guardado en Ver más tarde"
                    if value
                    else "Quitado de Ver más tarde"
                )
                self._set_status(message)
                self._notify(message)
            elif action == "queue":
                self._refresh_queue()
                message = (
                    f"🎬 En cola ({len(self._queue)})"
                    if value
                    else "Quitado de la cola"
                )
                self._set_status(message)
                self._notify(message)
            elif action == "copy":
                message = (
                    "🔗 URL copiada al portapapeles"
                    if value
                    else "No se pudo copiar la URL"
                )
                self._set_status(message)
                self._notify(
                    message, "information" if value else "warning"
                )

        def handle_result(action: str | None) -> None:
            if action is None:
                return
            if action == "download":
                self._start_download(r)
                return
            if action == "info":
                self._show_info(r)
                return
            self._audio_only_pref = action == "audio"
            self._last_mode = "audio" if action == "audio" else "video"
            self._iniciar_repeticion(idx, items, continuous)

        self.push_screen(ActionsScreen(r, on_toggle=handle_toggle), handle_result)

    def _show_info(self, item: dict) -> None:
        self.push_screen(InfoScreen(item))

    def _iniciar_repeticion(
        self,
        idx: int,
        items: list[dict] | None = None,
        continuous: bool = False,
    ) -> None:
        """Aviso inmediato y arranque de la reproducción."""
        items = items if items is not None else self._view_items()
        if 0 <= idx < len(items):
            self._set_status(f"Iniciando: {str(items[idx].get('title', ''))[:40]}…")
        self._play_session(idx, items, continuous)

    def _start_download(self, item: dict) -> None:
        if not item.get("url"):
            self._set_status("No se pudo descargar: falta la URL")
            return

        def apply(quality: str | None) -> None:
            if quality is None:
                return
            cfg = load_settings()
            cfg["download_quality"] = quality
            save_settings(cfg)
            task = self._downloads.add(
                str(item["url"]),
                str(item.get("title", "Sin título")),
                quality,
            )
            self._refresh_downloads()
            label = downloads.QUALITY_LABELS.get(quality, quality)
            message = f"En cola ({label}): {task.title[:40]}"
            self._set_status(message)
            self._notify(f"⬇️ {message}")

        self.push_screen(
            DownloadQualityScreen(str(item.get("title", ""))),
            apply,
        )

    def _playback_opts(self) -> dict:
        """Preferencias de reproducción guardadas por el usuario."""
        cfg = load_settings()
        try:
            volume = int(cfg.get("volume", 100))
        except (TypeError, ValueError):
            volume = 100
        return {
            "quality": cfg.get("quality", "best"),
            "speed": cfg.get("speed", 1.0),
            "volume": volume,
            "subtitles": bool(cfg.get("subtitles", False)),
            "audio_lang": str(cfg.get("audio_lang", "")),
            "sub_lang": str(cfg.get("sub_lang", "es,en")),
        }

    @work(thread=False, exclusive=True)
    async def _play_session(
        self,
        start_idx: int,
        items: list[dict],
        continuous: bool = False,
    ) -> None:
        try:
            view = self.query_one(ContentSwitcher).current
            table = (
                self._active_table()
                if view in ("search", "favorites", "later", "queue")
                else None
            )
            ytdl_path = player.find_tool("yt-dlp")
            opts = self._playback_opts()
            resume_on = bool(load_settings().get("resume", True))

            for i in range(start_idx, len(items)):
                item = items[i]
                if table is not None:
                    table.move_cursor(row=i)
                title = str(item.get("title", ""))[:50]
                self._set_status(
                    f"Reproduciendo [{i+1}/{len(items)}]: {escape(title)}"
                )
                if not item.get("url"):
                    continue
                if not self._incognito:
                    HistoryManager.add_watch(item)

                video_id = str(item.get("id") or "")
                start = player.resume_start(video_id) if (resume_on and video_id) else 0.0
                position = f"[{i+1}/{len(items)}] " if len(items) > 1 else ""
                if start:
                    self._set_status(
                        f"Reanudando «{title[:30]}» en {fmt_duration(start)}"
                    )
                    label = f"Reanudando {position}".rstrip()
                    self._show_playing_notification(
                        f"⏯️ {label}: {title[:40]} ({fmt_duration(start)})"
                    )
                elif self._audio_only_pref:
                    label = f"Solo audio {position}".rstrip()
                    self._show_playing_notification(
                        f"🎵 {label}: {title[:40]}"
                    )
                else:
                    label = f"Reproduciendo {position}".rstrip()
                    self._show_playing_notification(
                        f"▶️ {label}: {title[:40]}"
                    )

                play_opts = dict(opts)
                play_opts["start"] = start
                play_opts["progress_key"] = video_id

                if self._audio_only_pref:
                    # Audio: await each track before starting the next
                    resultado = await self._play_audio_track(
                        item["url"], ytdl_path, item.get("title"), play_opts
                    )
                    self._clear_playing_notification()
                    player.consume_progress()
                    self._set_status(f"Audio: {title}")
                    if resultado is not None and resultado.error:
                        message = (
                            f"Error de mpv (código {resultado.returncode}): "
                            f"{resultado.error[:60]}"
                        )
                        self._set_status(message)
                        self._notify(f"❌ {message}", "error")
                    if not (continuous or self._autoplay):
                        break
                    continue

                if sys.platform != "win32":
                    with self.app.suspend():
                        resultado = await player.play_async(
                            item["url"],
                            audio_only=False,
                            ytdl_path=ytdl_path,
                            title=item.get("title"),
                            **play_opts,
                        )
                else:
                    resultado = await player.play_async(
                        item["url"],
                        audio_only=False,
                        ytdl_path=ytdl_path,
                        title=item.get("title"),
                        **play_opts,
                    )

                self._clear_playing_notification()
                player.consume_progress()

                if resultado.error:
                    message = (
                        f"Error de mpv (código {resultado.returncode}): "
                        f"{resultado.error[:60]}"
                    )
                    self._set_status(message)
                    self._notify(f"❌ {message}", "error")

                if not (continuous or self._autoplay) or resultado.returncode == 4:
                    break

        except RuntimeError as exc:
            message = f"No se pudo iniciar el reproductor: {exc}"
            self._set_status(message)
            self._notify(f"❌ {message}", "error")
        except Exception as e:
            message = f"Error de reproducción: {e}"
            self._set_status(message)
            self._notify(f"❌ {message}", "error")
        finally:
            # Pase lo que pase, el aviso fijo no debe quedarse colgado.
            self._clear_playing_notification()

        self._set_status("Sesión de reproducción finalizada")

    async def _play_audio_track(
        self,
        url: str,
        ytdl_path: str | None,
        title: str | None = None,
        opts: dict | None = None,
    ) -> player.MpvResult | None:
        """Play one audio track, awaiting mpv to finish (keeps TUI responsive)."""
        try:
            return await player.play_async(
                url, audio_only=True, ytdl_path=ytdl_path, title=title, **(opts or {})
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_status("Fallo en la reproducción de audio")
            return None


    # ── Actions ───────────────────────────────────────────────────────────────

    def action_toggle_autoplay(self) -> None:
        self._autoplay = not self._autoplay
        message = f"Autoplay: {'ACTIVADO' if self._autoplay else 'DESACTIVADO'}"
        self._set_status(message)
        self._notify(message)

    # ── Biblioteca, orden y borrado ───────────────────────────────────────────

    def action_toggle_favorite(self) -> None:
        self._toggle_library("favorites")

    def action_toggle_later(self) -> None:
        self._toggle_library("later")

    def _toggle_library(self, which: str) -> None:
        view = self.query_one(ContentSwitcher).current
        if view not in ("search", "favorites", "later", "queue"):
            message = "Este atajo no está disponible en esta pestaña"
            self._set_status(message)
            self._notify(message, "warning")
            return

        items = self._view_items()
        table = self._active_table()
        idx = table.cursor_row
        if not (0 <= idx < len(items)):
            self._set_status("Selecciona un vídeo primero")
            return

        item = items[idx]
        added = Library.toggle(which, item)
        label = Library.label(which)
        title = str(item.get("title", ""))[:40]
        if added:
            message = f"⭐ Añadido a {label}: {title}"
        else:
            message = f"Quitado de {label}: {title}"
        self._set_status(message)
        self._notify(message)

        self._refresh_library("favorites")
        self._refresh_library("later")

        # Si estamos viendo la lista afectada, recolocamos el cursor.
        if view == which:
            remaining = len(self._view_items())
            if remaining:
                table.move_cursor(row=min(idx, remaining - 1))

    def action_add_to_queue(self) -> None:
        view = self.query_one(ContentSwitcher).current
        if view not in ("search", "favorites", "later"):
            message = "Añade a la cola desde Buscar, Favoritos o Ver más tarde"
            self._set_status(message)
            self._notify(message, "warning")
            return
        items = self._view_items()
        table = self._active_table()
        idx = table.cursor_row
        if not (0 <= idx < len(items)):
            self._set_status("Selecciona un vídeo primero")
            return
        item = items[idx]
        added = QueueStore.add(item)
        self._refresh_queue()
        title = str(item.get("title", ""))[:40]
        if added:
            message = f"🎬 En cola ({len(self._queue)}): {title}"
        else:
            message = f"Ya estaba en la cola: {title}"
        self._set_status(message)
        self._notify(message, "information" if added else "warning")

    def action_play_queue(self) -> None:
        self._refresh_queue()
        if not self._queue:
            self._set_status("La cola está vacía")
            self._notify("La cola está vacía", "warning")
            return
        self._open_actions(0, list(self._queue), continuous=True)

    def action_clear_queue(self) -> None:
        QueueStore.clear()
        self._refresh_queue()
        self._set_status("Cola vaciada")
        self._notify("🗑️ Cola vaciada")

    def action_resume_downloads(self) -> None:
        if self.query_one(ContentSwitcher).current != "downloads":
            message = "La tecla R funciona en la pestaña Descargas"
            self._set_status(message)
            self._notify(message, "warning")
            return
        count = self._downloads.resume_all()
        self._refresh_downloads()
        if count:
            message = f"Reanudando {count} descarga(s)…"
        else:
            message = "No hay descargas pendientes"
        self._set_status(message)
        self._notify(message, "information" if count else "warning")

    def action_open_downloads(self) -> None:
        folder = downloads.DOWNLOAD_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            self._set_status(f"Abriendo carpeta: {folder}")
            self._notify("📂 Abriendo la carpeta de descargas")
        except Exception as exc:  # noqa: BLE001
            message = f"No se pudo abrir la carpeta: {exc}"
            self._set_status(message)
            self._notify(f"❌ {message}", "error")

    def action_clear_finished_downloads(self) -> None:
        removed = self._downloads.clear_finished()
        self._refresh_downloads()
        if removed:
            message = f"{removed} descarga(s) completadas quitadas de la lista"
        else:
            message = "No hay descargas completadas"
        self._set_status(message)
        self._notify(message, "information" if removed else "warning")

    def action_load_more(self) -> None:
        if self.query_one(ContentSwitcher).current != "search":
            self._set_status("Cargar más solo funciona en la pestaña de búsqueda")
            return
        if not self._search_query:
            self._set_status("Primero haz una búsqueda")
            return
        self._search_count += 15
        self._run_search()

    def action_sort_results(self) -> None:
        def apply(result: tuple[str, bool] | None) -> None:
            if result is None:
                return
            key, reverse = result
            self._sort_key, self._sort_reverse = key, reverse
            cfg = load_settings()
            cfg.update({"sort_key": key, "sort_reverse": reverse})
            save_settings(cfg)
            if not self._results:
                self._set_status("No hay resultados que ordenar")
                return
            self._results = self._apply_sort(self._results)
            self._redraw_results()

        self.push_screen(
            SortScreen(self._sort_key, self._sort_reverse),
            apply,
        )

    def action_delete_entry(self) -> None:
        view = self.query_one(ContentSwitcher).current
        focused_id = getattr(self.focused, "id", "") or ""

        if view == "history":
            if focused_id == "watch-table":
                self._delete_watch_entry()
            elif focused_id == "history-table":
                self._delete_search_entry()
            else:
                self._set_status("Entra en una tabla del historial para borrar")
            return

        if view in ("favorites", "later"):
            items = self._view_items()
            table = self._active_table()
            idx = table.cursor_row
            if not (0 <= idx < len(items)):
                return
            item = items[idx]
            Library.remove(view, str(item.get("id", "")))
            self._refresh_library(view)
            message = (
                f"Quitado de {Library.label(view)}: "
                f"{str(item.get('title', ''))[:40]}"
            )
            self._set_status(message)
            self._notify(message)
            return

        if view == "queue":
            table = self.query_one("#queue-table", DataTable)
            idx = table.cursor_row
            if 0 <= idx < len(self._queue):
                title = str(self._queue[idx].get("title", ""))[:40]
                QueueStore.remove_at(idx)
                self._refresh_queue()
                message = f"Quitado de la cola: {title}"
                self._set_status(message)
                self._notify(message)
            return

        if view == "downloads":
            table = self.query_one("#downloads-table", DataTable)
            idx = table.cursor_row
            tasks = self._downloads.tasks()
            if not (0 <= idx < len(tasks)):
                return
            task = tasks[idx]
            if task.status == "downloading":
                message = "No puedes quitar una descarga en curso"
                self._set_status(message)
                self._notify(message, "warning")
                return
            self._downloads.remove(task.id)
            self._refresh_downloads()
            message = f"Quitado de la lista: {task.title[:40]}"
            self._set_status(message)
            self._notify(message)
            return

        self._set_status(
            "Entra en Favoritos, Ver más tarde, Cola, Descargas o Historial para borrar"
        )

    def _delete_search_entry(self) -> None:
        table = self.query_one("#history-table", DataTable)
        idx = table.cursor_row
        rows = HistoryManager.get_search_history()
        if not (0 <= idx < len(rows)):
            return
        query = str(rows[idx].get("query", ""))
        HistoryManager.remove_search(query)
        self._refresh_history()
        message = f"Búsqueda borrada: {query[:40]}"
        self._set_status(message)
        self._notify(message)

    def _delete_watch_entry(self) -> None:
        table = self.query_one("#watch-table", DataTable)
        idx = table.cursor_row
        rows = HistoryManager.get_watch_history()
        if not (0 <= idx < len(rows)):
            return
        vid_id = str(rows[idx].get("id", ""))
        title = str(rows[idx].get("title", ""))
        HistoryManager.remove_watch(vid_id)
        self._refresh_history()
        message = f"Vídeo borrado del historial: {title[:40]}"
        self._set_status(message)
        self._notify(message)

    def action_clear_history(self) -> None:
        def apply(ok: bool | None) -> None:
            if not ok:
                return
            HistoryManager.clear_search()
            HistoryManager.clear_watch()
            self._refresh_history()
            self._set_status("Historial borrado")
            self._notify("🗑️ Historial borrado")

        self.push_screen(
            ConfirmScreen(
                "Se borrará todo el historial de búsquedas y de vídeos vistos."
            ),
            apply,
        )

    def action_open_thumb(self) -> None:
        table = self.query_one("#results", DataTable)
        idx = table.cursor_row
        if 0 <= idx < len(self._results):
            r = self._results[idx]
            url = r.get("thumbnail")
            if url:
                cached = thumbnail._cache_path(url)
                if cached.exists():
                    import os
                    os.startfile(str(cached))
                    self._set_status(f"Abriendo miniatura HD: {str(r.get('title', ''))[:30]}")
                else:
                    self._set_status("La miniatura aún no se ha descargado.")

    def action_focus_search(self) -> None:
        self.action_switch_view("search")
        self.query_one("#search-bar", Input).focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # ── Paleta de comandos (Ctrl+P) — traducida al español ────────────────────

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Comandos que aparecen en la paleta de comandos, en español."""
        yield SystemCommand("Tema", "Cambiar el tema actual", self.action_change_theme)
        yield SystemCommand("Salir", "Cerrar la aplicación", self.action_quit)
        if screen.query("HelpPanel"):
            yield SystemCommand(
                "Teclas", "Ocultar el panel de teclas y ayuda", self.action_hide_help_panel
            )
        else:
            yield SystemCommand(
                "Teclas",
                "Mostrar la ayuda del widget enfocado y un resumen de teclas",
                self.action_show_help_panel,
            )
        if screen.maximized is not None:
            yield SystemCommand(
                "Restaurar", "Restaurar el widget a su tamaño normal", screen.action_minimize
            )
        elif screen.focused is not None and screen.focused.allow_maximize:
            yield SystemCommand(
                "Maximizar", "Maximizar el widget enfocado", screen.action_maximize
            )
        yield SystemCommand(
            "Captura",
            "Guardar una captura SVG de la pantalla actual",
            lambda: self.set_timer(0.1, self.deliver_screenshot),
        )
        yield SystemCommand(
            "Preferencias de reproducción",
            "Calidad, velocidad, idiomas, subtítulos y reanudar",
            self.action_preferences,
        )
        yield SystemCommand(
            "Ir a Favoritos",
            "Ver los vídeos que guardaste como favoritos",
            lambda: self.action_switch_view("favorites"),
        )
        yield SystemCommand(
            "Ir a Ver más tarde",
            "Ver los vídeos guardados para después",
            lambda: self.action_switch_view("later"),
        )
        yield SystemCommand(
            "Cargar más resultados",
            "Traer 15 resultados adicionales de la búsqueda actual",
            self.action_load_more,
        )
        yield SystemCommand(
            "Ordenar resultados",
            "Ordenar por duración, más vistos o título",
            self.action_sort_results,
        )
        yield SystemCommand(
            "Borrar todo el historial",
            "Eliminar el historial de búsquedas y de vídeos vistos",
            self.action_clear_history,
        )
        yield SystemCommand(
            "Ir a la Cola",
            "Ver los vídeos en cola de reproducción",
            lambda: self.action_switch_view("queue"),
        )
        yield SystemCommand(
            "Ir a Descargas",
            "Ver las descargas y su progreso",
            lambda: self.action_switch_view("downloads"),
        )
        yield SystemCommand(
            "Reproducir la cola",
            "Reproducir todos los vídeos en cola, uno tras otro",
            self.action_play_queue,
        )
        yield SystemCommand(
            "Vaciar la cola",
            "Quitar todos los vídeos de la cola",
            self.action_clear_queue,
        )
        yield SystemCommand(
            "Abrir carpeta de descargas",
            "Abrir en el explorador la carpeta ~/Downloads/yt-tui",
            self.action_open_downloads,
        )
        yield SystemCommand(
            "Reanudar descargas",
            "Volver a empezar las descargas pendientes o con error",
            self.action_resume_downloads,
        )
        yield SystemCommand(
            "Limpiar descargas completadas",
            "Quitar de la lista las descargas ya terminadas",
            self.action_clear_finished_downloads,
        )

    def action_preferences(self) -> None:
        """Abre la ventana de preferencias de reproducción."""

        def apply(result: dict | None) -> None:
            if result is None:
                return
            cfg = load_settings()
            cfg.update(result)
            save_settings(cfg)
            old_width = self._thumb_width
            self._apply_thumb_size()
            if self._thumb_width != old_width:
                self._refresh_thumb()
            self._set_status("Preferencias de reproducción guardadas")

        self.push_screen(PreferencesScreen(), apply)

    def action_command_palette(self) -> None:
        """Abre la paleta de comandos con el texto en español."""
        if self.use_command_palette and not CommandPalette.is_open(self):
            self.push_screen(
                PaletaComandos(id="--command-palette", placeholder="Buscar comandos…")
            )

    def action_change_theme(self) -> None:
        """Muestra el selector de temas con el texto en español."""
        self.push_screen(
            PaletaComandos(providers=[ThemeProvider], placeholder="Buscar temas…")
        )

    def action_switch_view(self, view: str, focus: bool = True) -> None:
        self.query_one(ContentSwitcher).current = view
        positions = {
            "search": 0,
            "favorites": 1,
            "later": 2,
            "queue": 3,
            "downloads": 4,
            "history": 5,
        }
        if view in positions:
            self.query_one("#nav-list").index = positions[view]

        # Refresca siempre el contenido de la vista.
        if view == "search":
            if focus:
                self.query_one("#search-bar").focus()
            return
        if view == "favorites":
            self._refresh_library("favorites")
        elif view == "later":
            self._refresh_library("later")
        elif view == "queue":
            self._refresh_queue()
        elif view == "downloads":
            self._refresh_downloads()
        else:
            self._refresh_history()

        if focus:
            self._focus_active_table()

    @on(ListView.Highlighted, "#nav-list")
    def handle_nav_highlight(self, event: ListView.Highlighted) -> None:
        """Al mover el resaltado con ↑/↓, la pantalla cambia sola."""
        view = self._nav_view(event.item.id if event.item else "")
        if view and view != self.query_one(ContentSwitcher).current:
            self.action_switch_view(view, focus=False)

    @on(ListView.Selected, "#nav-list")
    def handle_nav(self, event: ListView.Selected) -> None:
        view = self._nav_view(event.item.id or "")
        if view:
            self.action_switch_view(view)

    @staticmethod
    def _nav_view(item_id: str) -> str:
        return {
            "nav-search": "search",
            "nav-favorites": "favorites",
            "nav-later": "later",
            "nav-queue": "queue",
            "nav-downloads": "downloads",
            "nav-history": "history",
        }.get(item_id or "", "")

    # ── Navegación con flechas ────────────────────────────────────────────────

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Desactiva ← / → cuando escribimos texto o hay una ventana abierta."""
        if action in ("focus_sidebar", "focus_content"):
            if isinstance(self.screen, ModalScreen):
                return False
            if isinstance(self.focused, Input):
                return False
        return True

    def action_focus_sidebar(self) -> None:
        """← : ir al menú lateral."""
        self.query_one("#nav-list", ListView).focus()

    def action_focus_content(self) -> None:
        """→ : ir a la lista de la vista actual."""
        self._focus_active_table()

    def action_focus_results(self) -> None:
        """↓ : desde la barra de búsqueda, bajar a los resultados."""
        if isinstance(self.screen, ModalScreen):
            return
        if self.query_one(ContentSwitcher).current != "search":
            return
        if not isinstance(self.focused, Input):
            return
        self._focus_active_table()

    def _focus_active_table(self) -> None:
        table = self._active_table()
        table.focus()
        if table.row_count and table.cursor_row < 0:
            table.move_cursor(row=0)

    @on(DataTable.RowSelected, "#history-table")
    def handle_history_select(self, event: DataTable.RowSelected) -> None:
        row_data = self.query_one("#history-table", DataTable).get_row_at(event.cursor_row)
        query = row_data[0]
        self.query_one("#search-bar", Input).value = str(query)
        self.action_switch_view("search")
        self.handle_search(Input.Submitted(self.query_one("#search-bar", Input), str(query)))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        ap_status = "[green]AP:ON[/]" if self._autoplay else "[red]AP:OFF[/]"
        incog_prefix = f"[yellow]INCOG[/] {ap_status}" if self._incognito else ap_status
        self.query_one("#status", Static).update(f"{incog_prefix} | {msg}")

    def _notify(
        self,
        message: str,
        severity: str = "information",
        timeout: float | None = None,
    ) -> None:
        """Muestra un aviso flotante (toast). Nunca lanza errores."""
        cfg = load_settings()
        if not bool(cfg.get("notify_enabled", True)):
            return
        if timeout is None:
            try:
                timeout = float(cfg.get("notify_timeout", 5))
            except (TypeError, ValueError):
                timeout = 5.0
        try:
            # markup=False: los títulos pueden traer "[...]" y no deben
            # interpretarse como formato de Rich.
            self.notify(message, severity=severity, timeout=timeout, markup=False)
        except Exception:  # noqa: BLE001 - un aviso no debe romper la app
            logger.debug("No se pudo mostrar el aviso", exc_info=True)

    # Sin caducidad práctica: el aviso se retira a mano al terminar el vídeo.
    _PLAYING_NOTIFICATION_TIMEOUT = 10 ** 7

    def _show_playing_notification(self, message: str) -> None:
        """Muestra un aviso FIJO con lo que se está reproduciendo."""
        cfg = load_settings()
        if not bool(cfg.get("notify_enabled", True)):
            return
        self._clear_playing_notification()
        try:
            notification = Notification(
                message,
                severity="information",
                timeout=self._PLAYING_NOTIFICATION_TIMEOUT,
                markup=False,
            )
            self.post_message(Notify(notification))
            self._playing_notification = notification
        except Exception:  # noqa: BLE001 - un aviso no debe romper la app
            logger.debug("No se pudo mostrar el aviso de reproducción", exc_info=True)

    def _clear_playing_notification(self) -> None:
        """Retira el aviso fijo de reproducción, si sigue visible."""
        notification = self._playing_notification
        self._playing_notification = None
        if notification is None:
            return
        try:
            self._unnotify(notification)
        except Exception:  # noqa: BLE001 - nunca debe romper la reproducción
            logger.debug("No se pudo retirar el aviso de reproducción", exc_info=True)
