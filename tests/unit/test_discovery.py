"""Pruebas del descubrimiento de archivos (T-06; RF-01, CA-01.3)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mesa_ayuda.ingest.discovery import discover, file_sha256


def make_tree(root: Path) -> None:
    files = {
        "politica.md": "# Política",
        "rrhh/vacaciones.txt": "15 días",
        "rrhh/anual/reembolsos.PDF": "%PDF",  # extensión en mayúsculas
        "ti/soporte.docx": "PK",
        "~$soporte.docx": "bloqueo de Word",
        ".oculto.md": "oculto",
        ".git/config": "[core]",
        "imagen.png": "png",
        "notas.doc": "formato antiguo",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_lists_supported_files_recursively_with_posix_paths(tmp_path: Path) -> None:
    make_tree(tmp_path)

    result = discover(tmp_path)

    assert [item.source for item in result.files] == [
        "politica.md",
        "rrhh/anual/reembolsos.PDF",
        "rrhh/vacaciones.txt",
        "ti/soporte.docx",
    ]


def test_skips_temp_hidden_and_unsupported_with_reason(tmp_path: Path) -> None:
    make_tree(tmp_path)

    result = discover(tmp_path)

    reasons = {item.source: item.reason for item in result.skipped}
    assert reasons == {
        "~$soporte.docx": "office_temp",
        ".oculto.md": "hidden",
        ".git/": "hidden",  # no se recorre por dentro
        "imagen.png": "unsupported",
        "notas.doc": "unsupported",
    }


def test_hash_depends_only_on_content(tmp_path: Path) -> None:
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_bytes(b"mismo contenido")
    second.write_bytes(b"mismo contenido")

    assert file_sha256(first) == file_sha256(second)
    assert file_sha256(first) == "sha256:" + hashlib.sha256(b"mismo contenido").hexdigest()

    second.write_bytes(b"contenido distinto")
    assert file_sha256(first) != file_sha256(second)


def test_missing_folder_is_not_an_error(tmp_path: Path) -> None:
    result = discover(tmp_path / "no_existe")

    assert result.root_exists is False
    assert result.files == []


def test_empty_folder(tmp_path: Path) -> None:
    result = discover(tmp_path)

    assert result.root_exists is True
    assert result.files == [] and result.skipped == []
