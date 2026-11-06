"""Pruebas de los lectores por formato (T-07, T-08, T-09; RF-01, CA-01.1, CA-01.2, CA-01.4)."""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from mesa_ayuda.ingest.loaders import (
    UnreadableDocumentError,
    decode_text,
    load_document,
)
from scripts.simple_pdf import write_pdf

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


# --- PDF ------------------------------------------------------------------------------------


def test_pdf_has_one_section_per_page() -> None:
    sections = load_document(FIXTURES / "dos_paginas.pdf")

    assert [s.page for s in sections] == [1, 2]
    assert "anticipo es del 70 %" in sections[0].text
    assert "30 días (calendario)" in sections[1].text


def test_pdf_skips_blank_pages(tmp_path: Path) -> None:
    path = tmp_path / "con_blanco.pdf"
    write_pdf(path, ["Página con texto", "   ", "Tercera página"])

    assert [s.page for s in load_document(path)] == [1, 3]


def test_scanned_pdf_without_text_is_unreadable() -> None:
    with pytest.raises(UnreadableDocumentError, match="escaneado"):
        load_document(FIXTURES / "escaneado.pdf")


def test_corrupt_pdf_is_unreadable() -> None:
    with pytest.raises(UnreadableDocumentError, match="PDF dañado"):
        load_document(FIXTURES / "corrupto.pdf")


# --- DOCX -----------------------------------------------------------------------------------


def test_docx_sections_by_heading_style_and_tables(tmp_path: Path) -> None:
    document = docx.Document()
    document.add_heading("Guía de TI", level=0)
    document.add_paragraph("Texto introductorio.")
    document.add_heading("Prioridades", level=1)
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [("P1", "4 horas"), ("P2", "8 horas")], strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    document.add_heading("Contraseña", level=1)
    document.add_paragraph("Vence cada 90 días.", style="List Bullet")
    path = tmp_path / "guia.docx"
    document.save(str(path))

    sections = load_document(path)

    assert [(s.title, s.heading_path) for s in sections] == [
        ("Guía de TI", ("Guía de TI",)),
        ("Prioridades", ("Guía de TI", "Prioridades")),
        ("Contraseña", ("Guía de TI", "Contraseña")),
    ]
    assert sections[1].text == "P1 | 4 horas\nP2 | 8 horas"
    assert sections[2].text == "- Vence cada 90 días."


def test_corrupt_docx_is_unreadable() -> None:
    with pytest.raises(UnreadableDocumentError, match="DOCX dañado"):
        load_document(FIXTURES / "corrupto.docx")


def test_unsupported_extension_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "hoja.xlsx"
    path.write_bytes(b"x")

    with pytest.raises(UnreadableDocumentError, match="no soportada"):
        load_document(path)
