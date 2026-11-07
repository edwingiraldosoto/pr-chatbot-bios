"""Pruebas de la exportación de preguntas sin respuesta (T-33; RF-10, CA-10.2)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mesa_ayuda.storage import Storage
from scripts.unanswered import HEADER, main


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "bot.sqlite3"
    storage = Storage(path, history_length=6)
    storage.record_unanswered(
        111,
        "¿Cuál es la capital de Australia?",
        rewritten=None,
        best_score=0.7581,
        reason="below_threshold",
    )
    storage.record_unanswered(
        222,
        "¿y el del gerente?",
        rewritten='¿Cuál es el salario del gerente, "jefe"?',
        best_score=0.81321,
        reason="llm_not_found",
    )
    storage.record_unanswered(
        333, "hola", rewritten=None, best_score=None, reason="below_threshold"
    )
    storage.close()
    return path


def read(path: Path, delimiter: str = ",") -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream, delimiter=delimiter))


def test_exports_every_field_readable_by_excel(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "salida" / "sin_respuesta.csv"

    assert main(["--db", str(db), "--salida", str(output)]) == 0

    assert output.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM: Excel lee las tildes
    header, first, second, third = read(output)
    assert header == HEADER
    assert first[1:] == [
        "111",
        "¿Cuál es la capital de Australia?",
        "",
        "0.7581",
        "below_threshold",
    ]
    assert second[3] == '¿Cuál es el salario del gerente, "jefe"?'  # comillas y comas a salvo
    assert second[4] == "0.8132"
    assert third[4] == ""  # sin puntaje
    assert first[0].endswith("+00:00")
    assert "3 preguntas sin respuesta exportadas" in capsys.readouterr().out


def test_semicolon_for_spanish_excel(db: Path, tmp_path: Path) -> None:
    output = tmp_path / "x.csv"

    main(["--db", str(db), "--salida", str(output), "--separador", ";"])

    assert read(output, ";")[0] == HEADER


def test_since_filters_by_date(db: Path, tmp_path: Path) -> None:
    output = tmp_path / "x.csv"

    main(["--db", str(db), "--salida", str(output), "--desde", "2999-01-01"])

    assert read(output) == [HEADER]


def test_stdout_output(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(db), "--salida", "-"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == ",".join(HEADER) and len(lines) == 4


def test_missing_database_is_explained(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(tmp_path / "no.sqlite3")]) == 1

    assert "todavía no ha registrado preguntas" in capsys.readouterr().err


def test_invalid_date_is_rejected(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--db", str(db), "--desde", "01/11/2026"])

    assert "no es una fecha AAAA-MM-DD" in capsys.readouterr().err
