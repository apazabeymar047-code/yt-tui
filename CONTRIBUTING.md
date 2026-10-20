# Cómo contribuir a YT-TUI

¡Gracias por pasarte! Este proyecto es un fork con interfaz en español del [YT-TUI de Yashwanth Kumar](https://github.com/Yashwanth-Kumar-26/yt-tui). Cualquier ayuda cuenta: código, pruebas en tu sistema, documentación o simplemente reportar un fallo.

## Antes de abrir un issue

- Busca si ya existe uno abierto o cerrado con el mismo tema.
- Si es un fallo, incluye: sistema operativo, versión de Python, versiones de `mpv` y `chafa`, qué esperabas que pasara y qué pasó.
- Si es una idea grande (cambio de arquitectura, nueva dependencia), abre primero un issue para hablarlo. Así no trabajas en balde.

## Entorno de desarrollo

Requisitos: Python 3.10+, `git` y, para probar la reproducción, `mpv` y `chafa`.

```bash
git clone https://github.com/apazabeymar047-code/yt-tui.git
cd yt-tui
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install pytest pytest-asyncio
python -m yt_tui.cli
```

## Pruebas

```bash
python -m pytest -q
```

- Todo PR debe pasar la suite completa.
- Si arreglas un fallo, añade una prueba que falle antes del arreglo.
- La CI corre en Linux, macOS y Windows con Python 3.10, 3.11 y 3.12.

## Estilo

- PEP 8, 4 espacios, comillas dobles.
- Anotaciones de tipo cuando sea razonable (`from __future__ import annotations` ya está en el proyecto).
- Comentarios y textos de interfaz en español; nombres de variables y funciones en inglés.
- No añadas dependencias nuevas sin justificarlo en el issue o el PR: el proyecto presume de ser ligero.

## Flujo para mandar cambios

1. Haz un fork y crea una rama: `git checkout -b fix/reanudar-descargas`.
2. Commits pequeños, en imperativo: `arregla la reanudación de descargas`.
3. Abre el PR contra `main` explicando qué cambia y cómo lo probaste.
4. La CI tiene que estar en verde.
5. Si pido cambios, no es un rechazo: actualiza la rama y seguimos.

## Qué se acepta

- Arreglos de fallos con su prueba.
- Traducciones, documentación y capturas.
- Mejoras de rendimiento medidas.
- Funciones nuevas pequeñas que no añadan dependencias pesadas.

## Qué se evita

- Reescribir la interfaz entera sin haberlo hablado antes.
- Cambiar el formato de los archivos de `~/.yt-tui/` sin migración.
- Reformatear archivos completos mezclado con un arreglo: dificulta la revisión.

## Buenos primeros issues

Mira las etiquetas [`good first issue`](https://github.com/apazabeymar047-code/yt-tui/labels/good%20first%20issue) y [`help wanted`](https://github.com/apazabeymar047-code/yt-tui/labels/help%20wanted). Están pensados para no necesitar conocer todo el proyecto.

## Licencia

Al contribuir aceptas que tu aporte se publique bajo la licencia MIT del proyecto.
