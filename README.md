<p align="center">
  <img src="yt_tui/yt-tui.png" alt="YT-TUI" width="480" />
</p>

<h1 align="center">YT-TUI</h1>

<p align="center">
  <strong>Tu cliente de YouTube, dentro de la terminal.</strong><br/>
  Busca, guarda y reproduce vídeos sin abrir el navegador y sin claves de API.
</p>

<p align="center">
  <em>A lightweight, terminal-native YouTube client with a Spanish UI.</em>
</p>

<p align="center">
  <a href="https://github.com/apazabeymar047-code/yt-tui"><img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python 3.10+" /></a>
  <a href="https://github.com/apazabeymar047-code/yt-tui"><img src="https://img.shields.io/badge/Textual-TUI-green?logo=terminal" alt="Textual" /></a>
  <a href="https://github.com/apazabeymar047-code/yt-tui"><img src="https://img.shields.io/badge/Plataforma-Windows%20%7C%20macOS%20%7C%20Linux-blue" alt="Plataforma" /></a>
  <a href="https://github.com/apazabeymar047-code/yt-tui"><img src="https://img.shields.io/badge/Pruebas-50%20OK-brightgreen" alt="Pruebas" /></a>
  <a href="https://github.com/apazabeymar047-code/yt-tui"><img src="https://img.shields.io/badge/Licencia-MIT-yellow" alt="Licencia MIT" /></a>
</p>

---

## ✨ Características

- 🔎 **Búsqueda universal** — por palabras clave o pegando una URL de vídeo o de lista de reproducción.
- 🎬 **Reproducción con mpv** — en modo vídeo o **solo audio** (ahorra ancho de banda).
- 🖼️ **Miniaturas en la terminal** — arte ANSI generado con `chafa`, con **tamaño configurable**.
- ⭐ **Favoritos** y ⏰ **Ver más tarde** — guarda vídeos para después.
- 📋 **Cola de reproducción** — reproduce varios vídeos seguidos, con aviso `[i/n]` de la posición.
- ⬇️ **Panel de descargas** — calidad seleccionable, progreso, pausa y reanudación.
- 🕘 **Historial** — de búsquedas y de vídeos vistos.
- ▶️ **Continuar donde lo dejaste** — reanuda el vídeo en el minuto en que lo dejaste.
- 🔔 **Avisos flotantes** — confirmaciones en pantalla, con duración configurable.
- ⚙️ **Preferencias** — calidad, volumen, velocidad, idiomas de audio y subtítulos, reanudar, avisos y tamaño de la miniatura.
- ⌨️ **Navegación con flechas** — sin soltar el teclado.
- 🕶️ **Modo incógnito** — no guarda historial.
- 🇪🇸 **Interfaz en español**.
- 🪟 **Windows** — filtro que evita que los avisos del ratón se cuelen como texto en el buscador.

## 📦 Requisitos

- **Python 3.10 o superior**
- **[mpv](https://mpv.io/)** — reproducción de vídeo y audio
- **[chafa](https://hpjansson.org/chafa/)** — miniaturas (opcional: si falta, se desactivan solas)
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — se instala solo con las dependencias de Python
- Un terminal con soporte **Unicode/ANSI** (Windows Terminal, iTerm2, terminales modernas de Linux)

### Dependencias del sistema

```bash
# Windows (Winget)
winget install shinchiro.mpv chafa

# Windows (Scoop)
scoop install mpv chafa

# Fedora
sudo dnf install mpv chafa

# Ubuntu / Debian
sudo apt install mpv chafa

# Arch
sudo pacman -S mpv chafa

# macOS (Homebrew)
brew install mpv chafa
```

## 🚀 Instalación

### Opción 1 — Windows (automática)

```cmd
git clone https://github.com/apazabeymar047-code/yt-tui.git
cd yt-tui
setup.cmd
```

El script instala `uv` si hace falta, resuelve Python 3.10+ e instala `yt-tui` como comando global.

### Opción 2 — Manual (cualquier sistema)

```bash
git clone https://github.com/apazabeymar047-code/yt-tui.git
cd yt-tui
python -m pip install -r requirements.txt
python -m yt_tui.cli
```

> No hace falta instalar el paquete: `python -m yt_tui.cli` arranca la app directamente desde la carpeta.

### Opción 3 — Como comando global (opcional)

```bash
cd yt-tui
python -m pip install -e .
yt-tui
```

## 🎮 Uso

```bash
# Modo normal — guarda historial
python -m yt_tui.cli

# Modo incógnito — no guarda historial
python -m yt_tui.cli incog
```

1. La app arranca con el **buscador** enfocado. Escribe algo o pega una URL y pulsa **Enter**.
2. Muévete por los resultados con **↑ / ↓**: la miniatura y los datos se actualizan al momento.
3. Pulsa **Enter** sobre un vídeo para abrir el **menú de acciones**:

   | Acción | Qué hace |
   |---|---|
   | 🎬 Reproducir | Abre mpv en modo vídeo |
   | 🎵 Solo audio | Reproduce sin vídeo |
   | ⬇️ Descargar | Elige calidad y descarga |
   | ⭐ Favorito | Añade o quita de Favoritos |
   | ⏰ Ver más tarde | Añade o quita de la lista |
   | 🎬 A la cola | Añade o quita de la cola |
   | 🔗 Copiar URL | Copia el enlace al portapapeles |
   | ℹ️ Información | Ficha con datos y progreso |

4. Dentro de mpv, pulsa **`q`** para volver a YT-TUI.
5. Con **autoplay** activado, el siguiente vídeo de la cola empieza solo.

## ⌨️ Atajos de teclado

| Tecla | Acción |
|---|---|
| `F1` | Mostrar / ocultar la ayuda |
| `/` | Ir a la barra de búsqueda |
| `↑` / `↓` | Moverse por los resultados |
| `←` / `→` | Ir al menú lateral / volver al panel |
| `Enter` | Menú de acciones del vídeo |
| `f` | Añadir / quitar de Favoritos |
| `l` | Añadir / quitar de Ver más tarde |
| `q` | Añadir / quitar de la cola |
| `m` | Cargar más resultados |
| `o` | Ordenar resultados |
| `t` | Abrir la miniatura en HD |
| `Supr` | Borrar la entrada seleccionada |
| `r` | Reanudar descargas (pestaña Descargas) |
| `Ctrl+O` | Abrir la carpeta de descargas |
| `a` | Activar / desactivar autoplay |
| `s` | Pestaña de búsqueda |
| `h` | Pestaña de historial |
| `p` | Preferencias |
| `Ctrl+Q` | Salir |

### Durante la reproducción (mpv)

| Tecla | Acción |
|---|---|
| `Espacio` | Pausar / reanudar |
| `←` / `→` | Retroceder / avanzar 5 segundos |
| `↑` / `↓` | Saltar 60 segundos adelante / atrás |
| `9` / `0` | Subir / bajar el volumen |
| `m` | Silenciar |
| `q` | Detener y volver a YT-TUI |

## ⚙️ Preferencias (`p`)

- **Calidad de vídeo**: mejor disponible, 1080p, 720p o 480p
- **Volumen al reproducir**: 0 % – 100 %
- **Velocidad**: normal, 1.25x, 1.5x o 2x
- **Idioma del audio** y **de los subtítulos**
- **Activar subtítulos**
- **Reanudar donde quedé**
- **Avisos en pantalla** y su duración
- **Tamaño de la miniatura**: pequeña, mediana, grande o muy grande

## 💾 Datos y privacidad

Todo se guarda en local, en formato JSON legible:

```
~/.yt-tui/
├── settings.json          # preferencias
├── favorites.json         # favoritos
├── watch_later.json       # ver más tarde
├── queue.json             # cola de reproducción
├── search_history.json    # búsquedas
├── watch_history.json     # vídeos vistos
├── downloads.json         # estado de las descargas
└── progress.jsonl         # progreso de reproducción
```

Las descargas van a:

```
~/Downloads/yt-tui/
```

- **Modo incógnito** (`python -m yt_tui.cli incog`): no escribe en el historial ni en el progreso.
- No se envía nada a ningún servidor propio: la app habla directamente con YouTube a través de `yt-dlp`.

## 🧪 Pruebas

```bash
cd yt-tui
python -m pytest tests/ -q
```

**50 pruebas** cubren el reproductor, las utilidades, las descargas, el progreso y el filtro del ratón.

## 🪟 Notas para Windows

- mpv se abre en **su propia ventana**; al cerrarla con `q` vuelves a YT-TUI.
- El buscador **descarta los restos de avisos del ratón** que Textual deja escapar cuando el terminal envía secuencias cortadas (el clásico `[<32;124;5M`).
- Se recomienda **Windows Terminal** para ver bien las miniaturas y los colores.

## 🙏 Créditos

- Proyecto original: **[YT-TUI de Yashwanth Kumar](https://github.com/Yashwanth-Kumar-26/yt-tui)**.
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — acceso a YouTube sin API key.
- **[mpv](https://mpv.io/)** — reproducción de vídeo y audio.
- **[chafa](https://hpjansson.org/chafa/)** — miniaturas en la terminal.
- **[Textual](https://textual.textualize.io/)** y **[Rich](https://rich.readthedocs.io/)** — la interfaz.

## 📄 Licencia

MIT — consulta el archivo [LICENSE](LICENSE).
