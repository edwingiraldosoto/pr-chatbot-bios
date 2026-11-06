"""Fragmentación jerárquica con solapamiento (RF-02, CA-02.1 a CA-02.3).

Orden de preferencia para cortar: límite de sección → párrafo → línea → oración →
palabra → caracteres. Una sección corta queda entera en un fragmento; una larga se parte por
párrafos, y solo si un párrafo no cabe se baja al nivel siguiente.

Cada fragmento lleva antepuesta la ruta de encabezados de su sección (p. ej. "Política de
vacaciones > 2. Días de vacaciones"). Así un fragmento que solo dice "Son 15 días hábiles"
conserva de qué trata, lo que mejora la búsqueda. El encabezado cuenta dentro de
``chunk_size``: ningún fragmento supera ese tamaño.

El solapamiento repite el final del fragmento anterior (hasta ``chunk_overlap``
caracteres, empezando en un límite de palabra) dentro de la misma sección. Entre secciones
no hay solapamiento: mezclaría temas distintos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from mesa_ayuda.ingest.loaders import Section

HEADING_SEPARATOR = " > "

# Fin de oración: puntuación seguida de espacio. Los dos puntos y el punto y coma cierran
# también ítems de listas y pasos. Un punto precedido de dígito no cuenta: en "3. Topes"
# o "año 2026." es más probable un número de sección que un final.
_SENTENCE_END = re.compile(r"(?<=[^\d][.!?…:;])\s+")

#: Niveles de corte, del más grueso al más fino, con el separador que los une.
_LEVELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\n\s*\n"), "\n\n"),  # párrafo
    (re.compile(r"\n"), "\n"),  # línea (PDF y listas: el texto no trae líneas en blanco)
    (_SENTENCE_END, " "),  # oración
    (re.compile(r"\s+"), " "),  # palabra
)


@dataclass(frozen=True)
class Chunk:
    """Fragmento listo para calcular su embedding."""

    text: str  # encabezado + cuerpo
    section: str
    page: int
    index: int  # posición dentro del documento, desde 0


@dataclass(frozen=True)
class _Piece:
    text: str
    separator: str  # cómo se une con la pieza anterior al empaquetar


def _split(text: str, limit: int, level: int = 0, separator: str = "\n\n") -> list[_Piece]:
    """Parte ``text`` en piezas de como máximo ``limit`` caracteres.

    Solo baja de nivel (párrafo → línea → oración → palabra → caracteres) cuando una pieza
    del nivel actual no cabe. Cada pieza recuerda con qué separador se unía a la anterior,
    para que al empaquetar se conserven los saltos de párrafo y de línea.
    """
    if len(text) <= limit:
        return [_Piece(text, separator)]
    if level == len(_LEVELS):
        # Último recurso (URLs o cadenas sin espacios): corte por caracteres.
        return [
            _Piece(text[start : start + limit], separator if start == 0 else "")
            for start in range(0, len(text), limit)
        ]
    pattern, inner_separator = _LEVELS[level]
    pieces: list[_Piece] = []
    for position, part in enumerate(p.strip() for p in pattern.split(text) if p.strip()):
        pieces.extend(
            _split(part, limit, level + 1, separator if position == 0 else inner_separator)
        )
    return pieces


def _overlap_tail(text: str, overlap: int) -> str:
    """Últimos ``overlap`` caracteres de ``text``, sin empezar a mitad de palabra."""
    if overlap <= 0 or not text:
        return ""
    if len(text) <= overlap:
        return text
    tail = text[-overlap:]
    if not text[-overlap - 1].isspace():
        cut = re.search(r"\s", tail)
        if cut is None:
            return ""
        tail = tail[cut.end() :]
    # Si dentro del tramo empieza una oración, se arranca ahí: se lee mejor.
    sentence = _SENTENCE_END.search(tail)
    if sentence is not None and sentence.end() < len(tail):
        tail = tail[sentence.end() :]
    return tail.strip()


def _pack(pieces: list[_Piece], limit: int, overlap: int) -> list[str]:
    bodies: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece.text
            continue
        candidate = current + piece.separator + piece.text
        if len(candidate) <= limit:
            current = candidate
            continue
        bodies.append(current)
        tail = _overlap_tail(current, overlap)
        # Si con el solapamiento la pieza no cabe, se acorta el solapamiento.
        separator = piece.separator or " "
        while tail and len(tail) + len(separator) + len(piece.text) > limit:
            tail = _overlap_tail(tail, len(tail) - 1)
        current = tail + separator + piece.text if tail else piece.text
    if current:
        bodies.append(current)
    return bodies


def _header(section: Section, chunk_size: int) -> str:
    header = HEADING_SEPARATOR.join(section.heading_path)
    limit = chunk_size // 4  # un encabezado no puede comerse el espacio del contenido
    if len(header) > limit:
        header = header[-limit:].lstrip()
    return header


def chunk_sections(sections: Iterable[Section], chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    """Convierte las secciones de un documento en fragmentos numerados.

    Args:
        sections: secciones devueltas por un lector, en orden.
        chunk_size: tamaño máximo de cada fragmento, en caracteres (``CHUNK_SIZE``).
        chunk_overlap: caracteres que se repiten del fragmento anterior (``CHUNK_OVERLAP``).
    """
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap debe ser >= 0 y menor que chunk_size")

    chunks: list[Chunk] = []
    for section in sections:
        header = _header(section, chunk_size)
        prefix = f"{header}\n" if header else ""
        limit = chunk_size - len(prefix)
        overlap = min(chunk_overlap, limit // 2)
        for body in _pack(_split(section.text, limit), limit, overlap):
            chunks.append(
                Chunk(
                    text=prefix + body,
                    section=section.title,
                    page=section.page,
                    index=len(chunks),
                )
            )
    return chunks
