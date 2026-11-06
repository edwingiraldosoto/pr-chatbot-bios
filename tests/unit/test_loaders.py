"""Pruebas de los lectores por formato (T-07, T-08, T-09; RF-01, CA-01.1, CA-01.2, CA-01.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_ayuda.ingest.loaders import (
    UnreadableDocumentError,
    decode_text,
    load_document,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# --- Markdown y texto -----------------------------------------------------------------------


def test_markdown_sections_follow_headings(tmp_path: Path) -> None:
    path = tmp_path / "politica.md"
    path.write_text(
        "Introducción sin título.\n\n"
        "# Política de vacaciones\n\n"
        "## 2. Días de vacaciones\n\n15 días hábiles.\n\n"
        "### Excepciones\n\nAprendices no.\n\n"
        "## 3. Solicitud\n\nCon 15 días de anticipación.\n",
        encoding="utf-8",
    )

    sections = load_document(path)

    assert [(s.title, s.heading_path, s.text) for s in sections] == [
        ("", (), "Introducción sin título."),
        (
            "2. Días de vacaciones",
            ("Política de vacaciones", "2. Días de vacaciones"),
            "15 días hábiles.",
        ),
        (
            "Excepciones",
            ("Política de vacaciones", "2. Días de vacaciones", "Excepciones"),
            "Aprendices no.",
        ),
        (
            "3. Solicitud",
            ("Política de vacaciones", "3. Solicitud"),
            "Con 15 días de anticipación.",
        ),
    ]


def test_markdown_ignores_hashes_inside_code_blocks(tmp_path: Path) -> None:
    path = tmp_path / "guia.md"
    path.write_text("# Guía\n\n```bash\n# esto es un comentario\nls\n```\n", encoding="utf-8")

    [section] = load_document(path)

    assert section.title == "Guía"
    assert "# esto es un comentario" in section.text


def test_txt_in_utf8_with_bom(tmp_path: Path) -> None:
    path = tmp_path / "nota.txt"
    path.write_bytes("﻿Auxilio de conectividad: $85.000".encode())

    [section] = load_document(path)

    assert section.text == "Auxilio de conectividad: $85.000"
    assert (section.title, section.page) == ("", 0)


def test_txt_in_windows_1252_keeps_accents() -> None:
    # CA-01.2: archivo guardado con el Bloc de notas antiguo (cp1252, CRLF).
    [section] = load_document(FIXTURES / "cp1252.txt")

    assert "Política de baños: la señora Muñoz aprobó el año fiscal." in section.text
    assert "€20" in section.text
    assert "\r" not in section.text


def test_decode_never_fails_on_arbitrary_bytes() -> None:
    assert decode_text(bytes(range(256)))  # bytes que cp1252 no define → Latin-1


@pytest.mark.parametrize("content", [b"", b"   \n\n\t  "])
def test_empty_text_file_is_unreadable(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "vacio.md"
    path.write_bytes(content)

    with pytest.raises(UnreadableDocumentError, match="no tiene texto"):
        load_document(path)
