"""Pruebas de los adaptadores con el SDK real y un transporte HTTP simulado (T-21–T-23).

Cada SDK se crea con un ``httpx2.MockTransport``: se ejercita el código real del SDK (cómo
arma la petición, cómo interpreta la respuesta y qué excepción lanza) sin red.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from mesa_ayuda.llm.anthropic import AnthropicProvider, supports_effort
from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMTimeoutError,
    TransientLLMError,
)

Handler = Callable[[httpx2.Request], httpx2.Response]
MESSAGES = [
    ChatMessage("user", "¿Vacaciones?"),
    ChatMessage("assistant", "15 días [1]."),
    ChatMessage("user", "¿y?"),
]


class Recorder:
    """Transporte que guarda el cuerpo JSON de cada petición y responde con ``reply``."""

    def __init__(self, reply: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._reply = reply

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self._reply(request)


def status(code: int, body: dict[str, Any] | None = None) -> Handler:
    return lambda request: httpx2.Response(
        code, json=body or {"error": {"message": "x"}}, request=request
    )


def raise_timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("lento", request=request)


def raise_network(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("sin red", request=request)


async def call(provider: Any) -> Any:
    return await provider.complete("SISTEMA", MESSAGES, temperature=0.1, max_tokens=300)


# --- Anthropic -----------------------------------------------------------------------------

ANTHROPIC_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [{"type": "text", "text": "  Tienes 15 días [1].  "}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 120, "output_tokens": 9},
}


def anthropic_provider(handler: Handler, model: str = "claude-haiku-5-5") -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="k",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicProvider(api_key="k", model=model, timeout_seconds=5, client=client)


async def test_anthropic_request_and_response() -> None:
    recorder = Recorder(status(200, ANTHROPIC_OK))

    result = await call(anthropic_provider(recorder))

    body = recorder.bodies[0]
    assert body["system"] == "SISTEMA" and body["max_tokens"] == 300
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body  # R-05: el SDK 2.6 la retiró
    assert (result.text, result.input_tokens, result.output_tokens) == (
        "Tienes 15 días [1].",
        120,
        9,
    )


async def test_anthropic_older_models_get_no_effort() -> None:
    recorder = Recorder(status(200, ANTHROPIC_OK))

    await call(anthropic_provider(recorder, model="claude-haiku-4-5"))

    assert "output_config" not in recorder.bodies[0]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-haiku-5-5", True),
        ("claude-sonnet-5-5", True),
        ("claude-opus-4-5", True),
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-5", False),
        ("claude-3-7-sonnet", False),
    ],
)
def test_supports_effort(model: str, expected: bool) -> None:
    assert supports_effort(model) is expected


@pytest.mark.parametrize(
    ("handler", "error", "match"),
    [
        (status(429), TransientLLMError, "429"),
        (status(529), TransientLLMError, "529"),
        (status(500), TransientLLMError, "500"),
        (status(401), LLMAuthError, "LLM_API_KEY"),
        (status(403), LLMAuthError, "403"),
        (status(404), LLMBadRequestError, "404"),
        (status(400), LLMBadRequestError, "400"),
        (status(422), LLMBadRequestError, "422"),
        (raise_timeout, LLMTimeoutError, "a tiempo"),
        (raise_network, TransientLLMError, "red"),
    ],
)
async def test_anthropic_errors_are_translated(
    handler: Handler, error: type[LLMError], match: str
) -> None:
    with pytest.raises(error, match=match):
        await call(anthropic_provider(handler))
