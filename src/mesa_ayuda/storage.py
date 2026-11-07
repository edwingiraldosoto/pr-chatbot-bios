"""Historial, preguntas sin respuesta, invitados y uso diario en SQLite.

RF-05, RF-10, CA-07.5 y RNF-08. Un solo archivo (``data/bot.sqlite3``) con las tablas
descritas en
``specs/data-model.md``. Las operaciones son cortas (milisegundos), así que se hacen en el
hilo del bot con un candado; la conexión admite uso desde varios hilos por si un script o
una tarea en segundo plano la comparte.

Reglas:

- Cada usuario conserva como máximo ``history_length`` mensajes; al guardar un turno se
  descartan los más antiguos (CA-05.2).
- El historial de un usuario solo se lee con su propio ID (CA-05.3).
- Todo persiste en disco entre reinicios (CA-05.4, CA-10.2).
- El cupo diario se descuenta en una transacción ``BEGIN IMMEDIATE``: dos mensajes
  simultáneos no pueden pasar ambos el último cupo (RNF-08).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from mesa_ayuda.llm.base import ChatMessage

UnansweredReason = Literal["below_threshold", "llm_not_found"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_history_user ON history (user_id, id);

CREATE TABLE IF NOT EXISTS unanswered (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    user_id     INTEGER NOT NULL,
    question    TEXT    NOT NULL,
    rewritten   TEXT,
    best_score  REAL,
    reason      TEXT    NOT NULL CHECK (reason IN ('below_threshold', 'llm_not_found'))
);

CREATE TABLE IF NOT EXISTS invited_users (
    user_id     INTEGER PRIMARY KEY,
    first_name  TEXT    NOT NULL DEFAULT '',
    code_hash   TEXT    NOT NULL,
    invited_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    day         TEXT    NOT NULL,
    user_id     INTEGER NOT NULL,
    questions   INTEGER NOT NULL,
    PRIMARY KEY (day, user_id)
);
"""


@dataclass(frozen=True)
class UnansweredQuestion:
    """Una fila de ``unanswered`` (CA-10.1)."""

    created_at: str
    user_id: int
    question: str
    rewritten: str | None
    best_score: float | None
    reason: str


@dataclass(frozen=True)
class InvitedUser:
    """Un invitado con el código vigente, para ``/invitados``."""

    user_id: int
    first_name: str
    invited_at: str
    questions_today: int


class Quota(StrEnum):
    """Resultado de :meth:`Storage.take_question`."""

    OK = "ok"
    USER_LIMIT = "user_limit"  # CA-R8.1
    TOTAL_LIMIT = "total_limit"  # CA-R8.2


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Storage:
    """Acceso a ``bot.sqlite3``.

    Args:
        path: Ruta del archivo; la carpeta se crea si no existe.
        history_length: ``HISTORY_LENGTH``, mensajes que se conservan por usuario. Con 0 no
            se guarda historial.
    """

    def __init__(self, path: Path, *, history_length: int) -> None:
        if history_length < 0:
            raise ValueError("history_length no puede ser negativo")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.history_length = history_length
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        """Cierra la conexión."""
        with self._lock:
            self._conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # --- Historial (RF-05) ------------------------------------------------------------------

    def history(self, user_id: int) -> list[ChatMessage]:
        """Últimos ``history_length`` mensajes del usuario, del más antiguo al más reciente."""
        if self.history_length == 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, self.history_length),
            ).fetchall()
        return [ChatMessage(role, content) for role, content in reversed(rows)]

    def add_turn(self, user_id: int, question: str, answer: str) -> None:
        """Guarda la pregunta y la respuesta (sin fuentes) y recorta lo más antiguo."""
        if self.history_length == 0:
            return
        now = _now()
        with self._transaction() as conn:
            conn.executemany(
                "INSERT INTO history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                [(user_id, "user", question, now), (user_id, "assistant", answer, now)],
            )
            conn.execute(
                """
                DELETE FROM history WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, self.history_length),
            )

    def reset(self, user_id: int) -> int:
        """Borra el historial del usuario (``/reset``); devuelve cuántos mensajes borró."""
        with self._transaction() as conn:
            return conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,)).rowcount

    # --- Preguntas sin respuesta (RF-10) ----------------------------------------------------

    def record_unanswered(
        self,
        user_id: int,
        question: str,
        *,
        rewritten: str | None,
        best_score: float | None,
        reason: UnansweredReason,
    ) -> None:
        """Registra una pregunta que terminó en "no encontrado" (CA-10.1)."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO unanswered (created_at, user_id, question, rewritten, best_score,"
                " reason) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), user_id, question, rewritten, best_score, reason),
            )

    def unanswered(self, *, since: str | None = None) -> list[UnansweredQuestion]:
        """Preguntas sin respuesta en orden cronológico, opcionalmente desde una fecha ISO."""
        query = (
            "SELECT created_at, user_id, question, rewritten, best_score, reason FROM unanswered"
        )
        params: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE created_at >= ?"
            params = (since,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY id", params).fetchall()
        return [UnansweredQuestion(*row) for row in rows]

    # --- Invitados (CA-07.5) ----------------------------------------------------------------

    def invite(self, user_id: int, *, first_name: str, code_hash: str) -> None:
        """Autoriza a un usuario que abrió el enlace de invitación vigente."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO invited_users (user_id, first_name, code_hash, invited_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET"
                " first_name = excluded.first_name, code_hash = excluded.code_hash,"
                " invited_at = excluded.invited_at",
                (user_id, first_name, code_hash, _now()),
            )

    def is_invited(self, user_id: int, code_hash: str) -> bool:
        """Si el usuario entró con el código vigente (otro código ya no vale)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM invited_users WHERE user_id = ? AND code_hash = ?",
                (user_id, code_hash),
            ).fetchone()
        return row is not None

    def revoke(self, user_id: int) -> bool:
        """Retira la invitación; ``False`` si el usuario no era invitado."""
        with self._transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM invited_users WHERE user_id = ?", (user_id,)
            ).rowcount
        return deleted > 0

    def invited_users(self, code_hash: str, *, day: str) -> list[InvitedUser]:
        """Invitados con el código vigente y sus preguntas del día; los más recientes primero."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT i.user_id, i.first_name, i.invited_at, COALESCE(u.questions, 0)
                FROM invited_users i
                LEFT JOIN daily_usage u ON u.user_id = i.user_id AND u.day = ?
                WHERE i.code_hash = ?
                ORDER BY i.invited_at DESC, i.user_id
                """,
                (day, code_hash),
            ).fetchall()
        return [InvitedUser(*row) for row in rows]

    # --- Uso diario (RNF-08) ----------------------------------------------------------------

    def take_question(
        self, user_id: int, *, day: str, user_limit: int | None, total_limit: int | None
    ) -> Quota:
        """Descuenta una pregunta del cupo del día si queda; si no, dice qué tope se alcanzó.

        Args:
            user_id: Quien pregunta.
            day: ``AAAA-MM-DD`` en la zona horaria del bot.
            user_limit: Tope del usuario (``None`` = sin tope, administradores).
            total_limit: Tope de todos (``None`` = sin tope, administradores). Las preguntas
                sin tope igual se cuentan en el total.
        """
        with self._transaction() as conn:
            own, total = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN user_id = ? THEN questions END), 0),"
                " COALESCE(SUM(questions), 0) FROM daily_usage WHERE day = ?",
                (user_id, day),
            ).fetchone()
            if user_limit is not None and own >= user_limit:
                return Quota.USER_LIMIT
            if total_limit is not None and total >= total_limit:
                return Quota.TOTAL_LIMIT
            conn.execute(
                "INSERT INTO daily_usage (day, user_id, questions) VALUES (?, ?, 1)"
                " ON CONFLICT (day, user_id) DO UPDATE SET questions = questions + 1",
                (day, user_id),
            )
        return Quota.OK

    def questions_on(self, day: str) -> int:
        """Preguntas de todos los usuarios en el día."""
        with self._lock:
            (total,) = self._conn.execute(
                "SELECT COALESCE(SUM(questions), 0) FROM daily_usage WHERE day = ?", (day,)
            ).fetchone()
        return int(total)
