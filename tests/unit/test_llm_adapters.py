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
import openai
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
from mesa_ayuda.llm.gemini import GeminiProvider
from mesa_ayuda.llm.openai import OpenAIProvider

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


# --- OpenAI --------------------------------------------------------------------------------

OPENAI_OK = {
    "id": "c1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-5-mini",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Sí [2]."},
        }
    ],
    "usage": {"prompt_tokens": 80, "completion_tokens": 4, "total_tokens": 84},
}


def openai_provider(handler: Handler, base_url: str | None = None) -> OpenAIProvider:
    client = openai.AsyncOpenAI(
        api_key="k",
        base_url=base_url,
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAIProvider(
        api_key="k", model="gpt-5-mini", timeout_seconds=5, base_url=base_url, client=client
    )


async def test_openai_official_api_uses_max_completion_tokens() -> None:
    recorder = Recorder(status(200, OPENAI_OK))

    result = await call(openai_provider(recorder))

    body = recorder.bodies[0]
    assert body["messages"][0] == {"role": "system", "content": "SISTEMA"}
    assert [m["role"] for m in body["messages"][1:]] == ["user", "assistant", "user"]
    assert body["temperature"] == 0.1 and body["max_completion_tokens"] == 300
    assert "max_tokens" not in body
    assert (result.text, result.input_tokens, result.output_tokens) == ("Sí [2].", 80, 4)


async def test_openai_compatible_api_uses_max_tokens() -> None:
    recorder = Recorder(status(200, OPENAI_OK))
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return recorder(request)

    await call(openai_provider(handler, base_url="http://localhost:11434/v1"))

    assert seen == ["http://localhost:11434/v1/chat/completions"]
    assert (
        recorder.bodies[0]["max_tokens"] == 300
        and "max_completion_tokens" not in recorder.bodies[0]
    )


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (status(429), TransientLLMError),
        (status(503), TransientLLMError),
        (status(401), LLMAuthError),
        (status(404), LLMBadRequestError),
        (raise_timeout, LLMTimeoutError),
        (raise_network, TransientLLMError),
    ],
)
async def test_openai_errors_are_translated(handler: Handler, error: type[LLMError]) -> None:
    with pytest.raises(error):
        await call(openai_provider(handler))


# --- Gemini --------------------------------------------------------------------------------

GEMINI_OK = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "Son 15 días [1]."}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 70, "candidatesTokenCount": 6, "totalTokenCount": 76},
}


class Counting:
    def __init__(self, handler: Handler) -> None:
        self.calls = 0
        self._handler = handler

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        return self._handler(request)


def gemini_provider(handler: Handler) -> GeminiProvider:
    return GeminiProvider(
        api_key="k",
        model="gemini-3-flash",
        timeout_seconds=5,
        httpx_async_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def test_gemini_request_and_response() -> None:
    recorder = Recorder(status(200, GEMINI_OK))

    result = await call(gemini_provider(recorder))

    body = recorder.bodies[0]
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["systemInstruction"]["parts"][0]["text"] == "SISTEMA"
    assert body["generationConfig"] == {"temperature": 0.1, "maxOutputTokens": 300}
    assert (result.text, result.input_tokens, result.output_tokens) == ("Son 15 días [1].", 70, 6)


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (429, TransientLLMError),
        (500, TransientLLMError),
        (403, LLMAuthError),
        (400, LLMBadRequestError),
    ],
)
async def test_gemini_errors_are_translated(code: int, error: type[LLMError]) -> None:
    handler = Counting(status(code, {"error": {"code": code, "message": "x", "status": "X"}}))

    with pytest.raises(error):
        await call(gemini_provider(handler))

    assert handler.calls == 1  # sin reintentos propios del SDK


@pytest.mark.parametrize(
    ("handler", "error"), [(raise_network, TransientLLMError), (raise_timeout, LLMTimeoutError)]
)
async def test_gemini_transport_errors_are_translated(
    handler: Handler, error: type[LLMError]
) -> None:
    counting = Counting(handler)

    with pytest.raises(error):
        await call(gemini_provider(counting))

    assert counting.calls == 1
