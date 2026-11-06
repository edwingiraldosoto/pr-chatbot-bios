"""Lectores por formato: convierten un archivo en una lista de secciones (RF-01).

Cada lector devuelve :class:`Section` con el texto, el título de la sección (si el formato
lo tiene) y la página (solo PDF). Esa información viaja en los metadatos del fragmento y es
la que permite citar "sección X" o "página N" en la respuesta (CA-02.3, CA-03.1).

Un archivo que no se puede leer o no tiene texto lanza :class:`UnreadableDocumentError`;
quien llama decide qué hacer (omitirlo y reportarlo como ilegible, CA-01.4).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Section:
    """Bloque de texto con su ubicación dentro del documento."""

    text: str
    title: str = ""  # encabezado más cercano; "" si no hay
    page: int = 0  # página en PDF (desde 1); 0 si no aplica
    heading_path: tuple[str, ...] = ()  # encabezados de nivel superior hasta ``title``


class UnreadableDocumentError(Exception):
    """El archivo está dañado, protegido o no tiene texto extraíble."""


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")


def decode_text(data: bytes) -> str:
    """Decodifica UTF-8 (con o sin BOM) y, si falla, Windows-1252 (CA-01.2).

    Los ``.txt`` guardados con el Bloc de notas antiguo o exportados de Excel suelen venir en
    cp1252; leerlos como UTF-8 rompería tildes y eñes. Los pocos bytes que cp1252 no define
    se leen como Latin-1 para no perder el documento entero.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _require_text(sections: list[Section]) -> list[Section]:
    sections = [section for section in sections if section.text]
    if not sections:
        raise UnreadableDocumentError("no tiene texto extraíble")
    return sections


class _HeadingTracker:
    """Arma secciones a medida que aparecen encabezados de distintos niveles."""

    def __init__(self) -> None:
        self.sections: list[Section] = []
        self._path: list[tuple[int, str]] = []
        self._lines: list[str] = []

    def heading(self, level: int, title: str) -> None:
        self._flush()
        while self._path and self._path[-1][0] >= level:
            self._path.pop()
        self._path.append((level, title))

    def line(self, text: str) -> None:
        self._lines.append(text)

    def finish(self) -> list[Section]:
        self._flush()
        return self.sections

    def _flush(self) -> None:
        text = _normalize("\n".join(self._lines))
        self._lines = []
        if text:
            titles = tuple(title for _, title in self._path)
            self.sections.append(
                Section(text=text, title=titles[-1] if titles else "", heading_path=titles)
            )


def load_markdown(path: Path) -> list[Section]:
    """Una sección por encabezado ``#``; ignora los ``#`` dentro de bloques de código."""
    tracker = _HeadingTracker()
    in_fence = False
    for line in decode_text(path.read_bytes()).splitlines():
        if _MD_FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _MD_HEADING.match(line)
        if match:
            tracker.heading(len(match.group(1)), match.group(2).strip())
        else:
            tracker.line(line)
    return _require_text(tracker.finish())


def load_text(path: Path) -> list[Section]:
    """Todo el archivo como una sola sección sin título."""
    return _require_text([Section(text=_normalize(decode_text(path.read_bytes())))])


LOADERS: dict[str, Callable[[Path], list[Section]]] = {
    ".md": load_markdown,
    ".txt": load_text,
}


def load_document(path: Path) -> list[Section]:
    """Lee ``path`` con el lector de su extensión.

    Raises:
        UnreadableDocumentError: archivo dañado, protegido, sin texto o con extensión
            no soportada.
    """
    loader = LOADERS.get(path.suffix.lower())
    if loader is None:
        raise UnreadableDocumentError(f"extensión no soportada: {path.suffix}")
    try:
        return loader(path)
    except UnreadableDocumentError:
        raise
    except OSError as error:
        raise UnreadableDocumentError(f"no se pudo abrir ({error.strerror})") from error
