"""Pruebas del historial y de las preguntas sin respuesta (T-26; RF-05, RF-10)."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mesa_ayuda.llm.base import ChatMessage
from mesa_ayuda.storage import Quota, Storage


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


# --- Invitados (T-51; CA-07.5) --------------------------------------------------------------


def test_invited_user_stays_authorized_after_a_restart(db: Path) -> None:
    first = Storage(db, history_length=6)
    first.invite(42, first_name="Luis", code_hash="hash-a")
    first.close()

    second = Storage(db, history_length=6)

    assert second.is_invited(42, "hash-a")
    assert not second.is_invited(43, "hash-a")


def test_changing_the_code_leaves_old_invitees_out(db: Path) -> None:
    storage = Storage(db, history_length=6)
    storage.invite(42, first_name="Luis", code_hash="hash-viejo")

    assert not storage.is_invited(42, "hash-nuevo")
    assert storage.invited_users("hash-nuevo", day="2026-11-06") == []

    storage.invite(42, first_name="Luis", code_hash="hash-nuevo")  # vuelve con el enlace nuevo
    assert storage.is_invited(42, "hash-nuevo")


def test_revoke(db: Path) -> None:
    storage = Storage(db, history_length=6)
    storage.invite(42, first_name="Luis", code_hash="h")

    assert storage.revoke(42) is True
    assert not storage.is_invited(42, "h")
    assert storage.revoke(42) is False


def test_invited_users_shows_todays_questions(db: Path) -> None:
    storage = Storage(db, history_length=6)
    storage.invite(42, first_name="Luis", code_hash="h")
    storage.invite(43, first_name="Ana", code_hash="h")
    storage.invite(44, first_name="Viejo", code_hash="otro")
    for _ in range(3):
        storage.take_question(42, day="2026-11-06", user_limit=50, total_limit=300)
    storage.take_question(42, day="2026-11-05", user_limit=50, total_limit=300)

    rows = storage.invited_users("h", day="2026-11-06")

    assert {(r.user_id, r.first_name, r.questions_today) for r in rows} == {
        (42, "Luis", 3),
        (43, "Ana", 0),
    }


# --- Uso diario (T-51; RNF-08) --------------------------------------------------------------


def test_user_limit_is_per_day(db: Path) -> None:
    # CA-R8.1
    storage = Storage(db, history_length=6)
    day = "2026-11-06"

    results = [storage.take_question(1, day=day, user_limit=2, total_limit=100) for _ in range(3)]

    assert results == [Quota.OK, Quota.OK, Quota.USER_LIMIT]
    assert storage.take_question(2, day=day, user_limit=2, total_limit=100) is Quota.OK
    assert storage.take_question(1, day="2026-11-07", user_limit=2, total_limit=100) is Quota.OK
    assert storage.questions_on(day) == 3  # el rechazado no cuenta


def test_total_limit_applies_to_everyone(db: Path) -> None:
    # CA-R8.2
    storage = Storage(db, history_length=6)
    day = "2026-11-06"
    storage.take_question(1, day=day, user_limit=10, total_limit=2)
    storage.take_question(2, day=day, user_limit=10, total_limit=2)

    assert storage.take_question(3, day=day, user_limit=10, total_limit=2) is Quota.TOTAL_LIMIT


def test_admins_have_no_limit_but_count_in_the_total(db: Path) -> None:
    # CA-R8.4
    storage = Storage(db, history_length=6)
    day = "2026-11-06"
    for _ in range(5):
        assert storage.take_question(9, day=day, user_limit=None, total_limit=None) is Quota.OK

    assert storage.questions_on(day) == 5
    assert storage.take_question(1, day=day, user_limit=10, total_limit=5) is Quota.TOTAL_LIMIT


def test_simultaneous_requests_never_exceed_the_limit(db: Path) -> None:
    # El descuento es atómico: 20 hilos compitiendo por 7 cupos dejan exactamente 7.
    storage = Storage(db, history_length=6)
    day = "2026-11-06"

    def take(user: int) -> Quota:
        return storage.take_question(user, day=day, user_limit=100, total_limit=7)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(take, range(20)))

    assert results.count(Quota.OK) == 7
    assert storage.questions_on(day) == 7
