"""Pruebas del historial y de las preguntas sin respuesta (T-26; RF-05, RF-10)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mesa_ayuda.llm.base import ChatMessage
from mesa_ayuda.storage import Storage


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "datos" / "bot.sqlite3"


def test_history_keeps_the_order_of_the_conversation(db: Path) -> None:
    storage = Storage(db, history_length=6)

    storage.add_turn(1, "¿Cuántos días de vacaciones?", "15 días hábiles.")
    storage.add_turn(1, "¿Y se acumulan?", "Hasta dos periodos.")

    assert storage.history(1) == [
        ChatMessage("user", "¿Cuántos días de vacaciones?"),
        ChatMessage("assistant", "15 días hábiles."),
        ChatMessage("user", "¿Y se acumulan?"),
        ChatMessage("assistant", "Hasta dos periodos."),
    ]


def test_only_the_last_history_length_messages_are_kept(db: Path) -> None:
    # CA-05.2: con HISTORY_LENGTH=4 sobreviven los dos últimos turnos.
    storage = Storage(db, history_length=4)
    for n in range(1, 5):
        storage.add_turn(7, f"pregunta {n}", f"respuesta {n}")

    assert [m.content for m in storage.history(7)] == [
        "pregunta 3",
        "respuesta 3",
        "pregunta 4",
        "respuesta 4",
    ]
    with sqlite3.connect(db) as conn:  # lo antiguo se borra, no solo se oculta
        assert conn.execute("SELECT COUNT(*) FROM history WHERE user_id = 7").fetchone() == (4,)


def test_odd_history_length_starts_with_an_assistant_message(db: Path) -> None:
    # El recorte es por mensajes; prompting.normalize_history descarta el sobrante.
    storage = Storage(db, history_length=3)
    storage.add_turn(1, "p1", "r1")
    storage.add_turn(1, "p2", "r2")

    assert [m.content for m in storage.history(1)] == ["r1", "p2", "r2"]


def test_history_of_one_user_never_reaches_another(db: Path) -> None:
    # CA-05.3
    storage = Storage(db, history_length=6)
    storage.add_turn(1, "mía", "respuesta para 1")
    storage.add_turn(2, "tuya", "respuesta para 2")

    assert [m.content for m in storage.history(1)] == ["mía", "respuesta para 1"]
    assert [m.content for m in storage.history(2)] == ["tuya", "respuesta para 2"]
    assert storage.history(3) == []


def test_reset_only_deletes_the_sender_history(db: Path) -> None:
    # CA-06.3
    storage = Storage(db, history_length=6)
    storage.add_turn(1, "p", "r")
    storage.add_turn(2, "p", "r")

    assert storage.reset(1) == 2
    assert storage.history(1) == []
    assert len(storage.history(2)) == 2
    assert storage.reset(1) == 0


def test_history_length_zero_stores_nothing(db: Path) -> None:
    storage = Storage(db, history_length=0)
    storage.add_turn(1, "p", "r")

    assert storage.history(1) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone() == (0,)


def test_everything_survives_a_restart(db: Path) -> None:
    # CA-05.4, CA-10.2
    first = Storage(db, history_length=6)
    first.add_turn(1, "p", "r")
    first.record_unanswered(
        1, "¿capital de Australia?", rewritten=None, best_score=0.71, reason="below_threshold"
    )
    first.close()

    second = Storage(db, history_length=6)

    assert [m.content for m in second.history(1)] == ["p", "r"]
    assert [q.question for q in second.unanswered()] == ["¿capital de Australia?"]


def test_unanswered_keeps_all_the_fields(db: Path) -> None:
    # CA-10.1: fecha, usuario, pregunta original, reformulada y mejor puntaje.
    storage = Storage(db, history_length=6)
    storage.record_unanswered(
        5,
        "¿y eso?",
        rewritten="¿El salario del gerente?",
        best_score=0.8132,
        reason="llm_not_found",
    )
    storage.record_unanswered(5, "hola", rewritten=None, best_score=None, reason="below_threshold")

    first, second = storage.unanswered()

    assert (first.user_id, first.question, first.rewritten, first.best_score, first.reason) == (
        5,
        "¿y eso?",
        "¿El salario del gerente?",
        0.8132,
        "llm_not_found",
    )
    assert first.created_at.endswith("+00:00")  # ISO 8601 con zona horaria
    assert (second.rewritten, second.best_score) == (None, None)


def test_unanswered_can_be_filtered_by_date(db: Path) -> None:
    storage = Storage(db, history_length=6)
    storage.record_unanswered(1, "vieja", rewritten=None, best_score=None, reason="below_threshold")

    assert len(storage.unanswered(since="2000-01-01")) == 1
    assert storage.unanswered(since="2999-01-01") == []


def test_invalid_reason_is_rejected_by_the_schema(db: Path) -> None:
    storage = Storage(db, history_length=6)

    with pytest.raises(sqlite3.IntegrityError):
        storage.record_unanswered(
            1,
            "p",
            rewritten=None,
            best_score=None,
            reason="otra",  # type: ignore[arg-type]
        )
    assert storage.unanswered() == []  # la transacción fallida no deja rastro


def test_negative_history_length_is_rejected(db: Path) -> None:
    with pytest.raises(ValueError, match="negativo"):
        Storage(db, history_length=-1)
