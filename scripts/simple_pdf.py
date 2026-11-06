"""Escritor mínimo de PDF de texto, sin dependencias.

Sirve para generar el PDF de ejemplo de ``knowledge_base/`` y los PDF de las pruebas sin
agregar una librería de generación (reportlab) solo para eso. Usa la fuente estándar
Helvetica con codificación WinAnsi, que cubre tildes, eñes y signos de apertura del español.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

PAGE_WIDTH, PAGE_HEIGHT = 612, 792  # carta, en puntos
MARGIN = 72
FONT_SIZE = 11
LEADING = 15
WRAP_COLUMNS = 82


def _escape(line: str) -> bytes:
    data = line.encode("cp1252")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content_stream(lines: list[str]) -> bytes:
    commands = [b"BT", f"/F1 {FONT_SIZE} Tf".encode(), f"{LEADING} TL".encode()]
    commands.append(f"{MARGIN} {PAGE_HEIGHT - MARGIN} Td".encode())
    for line in lines:
        commands.append(b"(" + _escape(line) + b") Tj T*")
    commands.append(b"ET")
    return b"\n".join(commands)


def wrap_page(text: str) -> list[str]:
    """Ajusta el texto de una página a líneas que caben en el ancho útil."""
    lines: list[str] = []
    for paragraph in text.strip().split("\n"):
        lines.extend(textwrap.wrap(paragraph, WRAP_COLUMNS) or [""])
    return lines


def write_pdf(path: Path, pages: list[str], *, empty_pages: bool = False) -> None:
    """Escribe un PDF con una página por elemento de ``pages``.

    Args:
        path: destino.
        pages: texto de cada página (las líneas se ajustan al ancho).
        empty_pages: si es verdadero, las páginas no llevan texto, como un PDF escaneado.
    """
    count = len(pages)
    page_ids = [3 + i for i in range(count)]
    content_ids = [3 + count + i for i in range(count)]
    font_id = 3 + 2 * count
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{pid} 0 R" for pid in page_ids)
            + f"] /Count {count} >>"
        ).encode(),
        font_id: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        ),
    }
    for pid, cid, text in zip(page_ids, content_ids, pages, strict=True):
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {cid} 0 R >>"
        ).encode()
        stream = b"" if empty_pages else _content_stream(wrap_page(text))
        objects[cid] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(output)
        output += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(output)
    total = max(objects) + 1
    output += f"xref\n0 {total}\n0000000000 65535 f \n".encode()
    for number in range(1, total):
        output += f"{offsets[number]:010d} 00000 n \n".encode()
    output += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(output))
