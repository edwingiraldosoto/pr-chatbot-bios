"""Guardas que se evalúan antes de procesar un mensaje (RF-07, RNF-05).

Orden (``specs/contracts/bot-commands.md``): chat privado → acceso → copias repetidas →
límite por minuto → validación del texto. No dependen de Telegram, para probarlas sin red.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from collections.abc import Callable, Iterable
from enum import StrEnum

PRIVATE_CHAT = "private"


def is_private_chat(chat_type: str | None) -> bool:
    """Solo se atienden chats privados (CA-07.4)."""
    return chat_type == PRIVATE_CHAT


class AccessControl:
    """Quién puede usar el bot y quién administra (CA-07.1–CA-07.3).

    La lista vacía no da acceso a nadie: la falla es cerrada.
    """

    def __init__(self, allowed: Iterable[int], admins: Iterable[int]) -> None:
        self._admins = frozenset(admins)
        self._authorized = frozenset(allowed) | self._admins

    def is_authorized(self, user_id: int) -> bool:
        """Si el usuario puede hacer preguntas."""
        return user_id in self._authorized

    def is_admin(self, user_id: int) -> bool:
        """Si el usuario puede usar ``/reindex``."""
        return user_id in self._admins


class RateDecision(StrEnum):
    """Resultado de :meth:`RateLimiter.check`."""

    ALLOW = "allow"
    WARN = "warn"  # primer mensaje por encima del límite: se avisa
    DROP = "drop"  # siguientes de la misma ventana: se descartan sin responder


class RateLimiter:
    """Ventana deslizante de 60 s por usuario, en memoria (CA-R5.4).

    Solo cuentan los mensajes aceptados. El aviso se da una vez por ventana: después, hasta
    que salga de la ventana el mensaje más antiguo, los demás se descartan en silencio.

    Args:
        limit: ``RATE_LIMIT_PER_MINUTE``.
        window_seconds: Tamaño de la ventana.
        clock: Reloj monótono (inyectable en las pruebas).
    """

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit debe ser al menos 1")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._accepted: dict[int, deque[float]] = {}
        self._warned_until: dict[int, float] = {}

    def check(self, user_id: int) -> RateDecision:
        """Registra un mensaje del usuario y dice qué hacer con él."""
        now = self._clock()
        accepted = self._accepted.setdefault(user_id, deque())
        while accepted and now - accepted[0] >= self.window_seconds:
            accepted.popleft()
        if len(accepted) < self.limit:
            accepted.append(now)
            self._warned_until.pop(user_id, None)
            return RateDecision.ALLOW
        if self._warned_until.get(user_id, float("-inf")) > now:
            return RateDecision.DROP
        self._warned_until[user_id] = accepted[0] + self.window_seconds
        return RateDecision.WARN


def clean_question(text: str | None) -> str | None:
    """Texto de la pregunta sin espacios sobrantes, o ``None`` si está vacío (CA-R5.3)."""
    if text is None:
        return None
    cleaned = text.strip()
    return cleaned or None


class DuplicateFilter:
    """Descarta copias del mismo mensaje enviadas seguidas por el mismo usuario (CA-R5.7).

    Algunos clientes de Telegram reenvían un mensaje varias veces (red inestable, Enter
    repetido). Se compara cada texto con el último aceptado de ese usuario, sin espacios
    sobrantes ni diferencias de mayúsculas; si es igual y llegó dentro de la ventana, es una
    copia. La ventana cuenta desde la primera copia, así que una ráfaga no la alarga.

    Args:
        window_seconds: ``DUPLICATE_WINDOW_SECONDS``; 0 desactiva el filtro.
        clock: Reloj monótono (inyectable en las pruebas).
    """

    def __init__(
        self, window_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if window_seconds < 0:
            raise ValueError("window_seconds no puede ser negativo")
        self.window_seconds = window_seconds
        self._clock = clock
        self._last: dict[int, tuple[str, float]] = {}

    def is_duplicate(self, user_id: int, text: str | None) -> bool:
        """Si el texto es una copia del último mensaje aceptado del usuario."""
        if self.window_seconds == 0 or text is None:
            return False
        key = " ".join(text.split()).casefold()
        if not key:
            return False
        now = self._clock()
        last = self._last.get(user_id)
        if last is not None and last[0] == key and now - last[1] < self.window_seconds:
            return True
        self._last[user_id] = (key, now)
        return False


def invite_fingerprint(code: str) -> str:
    """Huella del código de invitación que se guarda por invitado (CA-07.5)."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def invite_payload(text: str | None) -> str | None:
    """Parámetro de ``/start <código>`` (lo que Telegram envía al abrir el enlace)."""
    if text is None:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or parts[0].split("@", 1)[0] != "/start":
        return None
    return parts[1].strip()


def invite_matches(payload: str, code: str) -> bool:
    """Compara en tiempo constante, para no dar pistas del código por la demora."""
    return hmac.compare_digest(payload.encode("utf-8"), code.encode("utf-8"))
