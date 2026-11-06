"""Pruebas de los logs estructurados (T-04; RNF-03, RNF-06).

Lo crítico es que un secreto no se filtre por ningún camino: eventos propios, logs de
librerías de terceros y trazas de excepciones.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from mesa_ayuda.logging_setup import (
    REDACTED,
    bind_request,
    clear_request,
    configure_logging,
    get_logger,
    new_request_id,
)

TOKEN = "987654321:AAFakeTokenForTestsOnly_abcdefghijklmn"
API_KEY = "sk-test-fake-key-0123456789abcdef"


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("DEBUG", secrets=[TOKEN, API_KEY], stream=stream)
    yield stream
    clear_request()
    logging.getLogger().handlers.clear()


def read_events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_events_are_json_with_request_context(log_stream: io.StringIO) -> None:
    request_id = new_request_id()
    bind_request(request_id=request_id, user_id=42)

    get_logger("prueba").info("question_answered", chunks=2, latency_ms=830)

    [event] = read_events(log_stream)
    assert event["event"] == "question_answered"
    assert event["request_id"] == request_id
    assert event["user_id"] == 42
    assert event["level"] == "info"
    assert "timestamp" in event


def test_known_secrets_are_redacted_in_own_events(log_stream: io.StringIO) -> None:
    get_logger("prueba").error(
        "llm_error", detail=f"Invalid key {API_KEY}", nested={"url": f"/bot{TOKEN}/x"}
    )

    output = log_stream.getvalue()
    assert API_KEY not in output and TOKEN not in output
    assert REDACTED in output


def test_token_in_third_party_log_url_is_redacted(log_stream: io.StringIO) -> None:
    # Así registra httpx cada llamada de python-telegram-bot.
    logging.getLogger("httpx").warning(
        'HTTP Request: POST https://api.telegram.org/bot%s/getUpdates "HTTP/1.1 200 OK"', TOKEN
    )

    output = log_stream.getvalue()
    assert TOKEN not in output
    assert "getUpdates" in output


def test_httpx_info_is_silenced(log_stream: io.StringIO) -> None:
    logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/botX/getMe")

    assert log_stream.getvalue() == ""


def test_unknown_token_shaped_values_are_redacted(log_stream: io.StringIO) -> None:
    other_token = "123456789:AAOtherBotTokenNotConfigured_zyxwvuts"

    get_logger("prueba").warning("odd_message", text=f"token pegado: {other_token}")

    assert other_token not in log_stream.getvalue()


def test_exception_tracebacks_are_redacted(log_stream: io.StringIO) -> None:
    try:
        raise RuntimeError(f"fallo llamando con {API_KEY}")
    except RuntimeError:
        get_logger("prueba").exception("unexpected_error")

    [event] = read_events(log_stream)
    assert "RuntimeError" in str(event["exception"])
    assert API_KEY not in log_stream.getvalue()


def test_non_ascii_text_is_kept_readable(log_stream: io.StringIO) -> None:
    get_logger("prueba").info(
        "reindex_finished", summary="1 nuevo, 0 eliminados: política_vacaciones.md"
    )

    assert "política_vacaciones.md" in log_stream.getvalue()
