"""Exporta las preguntas sin respuesta a CSV (RF-10, CA-10.2).

Uso, desde la raíz del proyecto (con el bot encendido o apagado)::

    python -m scripts.unanswered                          # data/preguntas_sin_respuesta.csv
    python -m scripts.unanswered --desde 2026-11-01       # solo desde esa fecha
    python -m scripts.unanswered --salida -               # a la pantalla
    python -m scripts.unanswered --separador ";"          # Excel con configuración regional
                                                          # de Colombia o España

El archivo es UTF-8 con BOM, para que Excel muestre bien las tildes. Columnas: fecha
(UTC), usuario, pregunta, pregunta reformulada, mejor puntaje de similitud y motivo
(``below_threshold`` = nada pasó el umbral; ``llm_not_found`` = el modelo no encontró la
respuesta en los fragmentos).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

from mesa_ayuda.storage import Storage, UnansweredQuestion

DEFAULT_DB = Path("data") / "bot.sqlite3"
DEFAULT_OUTPUT = Path("data") / "preguntas_sin_respuesta.csv"
HEADER = ["fecha", "usuario", "pregunta", "pregunta_reformulada", "mejor_puntaje", "motivo"]


def write_csv(rows: Sequence[UnansweredQuestion], stream: TextIO, delimiter: str = ",") -> None:
    """Escribe las filas con encabezado."""
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows:
        score = "" if row.best_score is None else f"{row.best_score:.4f}"
        writer.writerow(
            [row.created_at, row.user_id, row.question, row.rewritten or "", score, row.reason]
        )


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} no es una fecha AAAA-MM-DD (ejemplo: 2026-11-01)"
        ) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.unanswered",
        description="Exporta las preguntas sin respuesta del bot a CSV.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"por defecto {DEFAULT_DB}")
    parser.add_argument(
        "--salida",
        default=str(DEFAULT_OUTPUT),
        help=f"archivo CSV, o '-' para la pantalla (por defecto {DEFAULT_OUTPUT})",
    )
    parser.add_argument("--desde", type=_date, help="solo desde esta fecha (AAAA-MM-DD)")
    parser.add_argument(
        "--separador", default=",", choices=[",", ";"], help="separador de columnas (',' o ';')"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada; devuelve 0 si exportó y 1 si no hay base de datos."""
    args = _parser().parse_args(argv)
    if not args.db.is_file():
        print(
            f"No existe {args.db}: el bot todavía no ha registrado preguntas "
            "(o usa --db con la ruta de DATA_DIR/bot.sqlite3).",
            file=sys.stderr,
        )
        return 1
    storage = Storage(args.db, history_length=0)
    try:
        rows = storage.unanswered(since=args.desde)
    finally:
        storage.close()

    if args.salida == "-":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        write_csv(rows, sys.stdout, args.separador)
        return 0
    output = Path(args.salida)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        write_csv(rows, stream, args.separador)
    print(f"{len(rows)} preguntas sin respuesta exportadas a {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
