"""Logs estructurados en JSON, con ID de solicitud y redacción de secretos.

Todo pasa por un único manejador del módulo ``logging``: tanto los eventos propios
(``structlog``) como los de librerías (``httpx``, ``telegram``, ``chromadb``). Así la
redacción de secretos se aplica también a mensajes que no escribimos nosotros. El caso
real que motiva esto: ``httpx`` registra en INFO la URL de cada llamada a Telegram, y esa
URL contiene el token del bot (``https://api.telegram.org/bot<TOKEN>/getUpdates``).
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from collections.abc import Iterable
from typing import IO, Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "***"

#: Formas típicas de credenciales, redactadas aunque no se conozca su valor exacto
#: (p. ej. el token de otro bot pegado en un mensaje de error).
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Token de bot de Telegram. Sin "\b" al inicio: en la URL ".../bot123:ABC.../getMe" no
    # hay límite de palabra entre "bot" y el número, y el token quedaría sin redactar.
    re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # API keys estilo OpenAI / Anthropic
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # API keys de Google
)

#: Librerías ruidosas o que registran URLs con el token. Se suben a WARNING; la redacción
#: sigue activa como segunda barrera si alguien baja el nivel.
NOISY_LOGGERS = ("httpx", "httpcore", "telegram.ext.Updater", "chromadb", "fastembed")


class SecretRedactor:
    """Procesador de structlog que reemplaza secretos en cualquier valor del evento."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Valores muy cortos generarían reemplazos falsos en texto normal.
        self._secrets = sorted({s for s in secrets if len(s) >= 8}, key=len, reverse=True)

    def redact(self, value: Any) -> Any:
        """Devuelve ``value`` con los secretos reemplazados, recorriendo dicts y listas."""
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, REDACTED)
            for pattern in SECRET_PATTERNS:
                value = pattern.sub(REDACTED, value)
            return value
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self.redact(item) for item in value)
        return value

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        """Redacta todos los valores del evento (interfaz de procesador de structlog)."""
        for key, value in list(event_dict.items()):
            event_dict[key] = self.redact(value)
        return event_dict


def _drop_color_message(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    # uvicorn y otras librerías agregan un duplicado con colores ANSI; no aporta en JSON.
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(
    level: str = "INFO",
    secrets: Iterable[str] = (),
    stream: IO[str] | None = None,
) -> None:
    """Configura structlog y ``logging`` para emitir una línea JSON por evento.

    Args:
        level: nivel mínimo (``LOG_LEVEL``).
        secrets: valores que nunca deben aparecer en los logs (token, API key).
        stream: destino; por defecto ``sys.stdout`` (lo que espera Docker).
    """
    redactor = SecretRedactor(secrets)
    if stream is None:
        stream = sys.stdout
        # La consola de Windows usa cp1252 por defecto; sin esto, una tilde o un emoji en
        # un mensaje rompe el log con UnicodeEncodeError.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.format_exc_info,
        _drop_color_message,
        # La redacción va al final, cuando la traza de una excepción ya es texto.
        redactor,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Devuelve un logger estructurado con el nombre del módulo."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def new_request_id() -> str:
    """Genera un ID corto para correlacionar todos los eventos de un mensaje."""
    return uuid.uuid4().hex[:12]


def bind_request(**values: Any) -> None:
    """Asocia valores (``request_id``, ``user_id``) a todos los eventos del contexto actual."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**values)


def clear_request() -> None:
    """Limpia el contexto de la solicitud actual."""
    structlog.contextvars.clear_contextvars()
