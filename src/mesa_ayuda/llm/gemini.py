"""Adaptador de Google Gemini con el SDK ``google-genai`` (T-23; CA-R1.4).

Es opcional: el paquete se instala con ``pip install -e ".[gemini]"``. Si falta y se elige
``LLM_PROVIDER=gemini``, el arranque falla con un mensaje que dice cómo instalarlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx2

from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMResult,
    LLMTimeoutError,
    TransientLLMError,
)

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types

    GENAI_INSTALLED = True
except ImportError:  # pragma: no cover - depende de lo instalado
    GENAI_INSTALLED = False


class GeminiProvider:
    """Llama a ``models.generate_content`` (cliente asíncrono ``client.aio``)."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        httpx_async_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if not GENAI_INSTALLED:
            raise LLMBadRequestError(
                'LLM_PROVIDER=gemini necesita el paquete opcional: pip install -e ".[gemini]"'
            )
        self.model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(timeout_seconds * 1000),  # milisegundos
                # Un solo intento: la política de reintentos es la de RetryingProvider.
                retry_options=genai_types.HttpRetryOptions(attempts=1),
                httpx_async_client=httpx_async_client,
            ),
        )

    def request(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Argumentos de ``generate_content`` (Gemini llama ``model`` al asistente)."""
        contents = [
            genai_types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system, temperature=temperature, max_output_tokens=max_tokens
        )
        return {"model": self.model, "contents": contents, "config": config}

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        """Genera la respuesta y traduce los errores del SDK."""
        try:
            response = await self._client.aio.models.generate_content(
                **self.request(system, messages, temperature=temperature, max_tokens=max_tokens)
            )
        except genai_errors.APIError as error:
            raise _translate(error) from error
        except (TimeoutError, httpx2.TimeoutException) as error:
            raise LLMTimeoutError("Gemini no respondió a tiempo") from error
        except (OSError, httpx2.TransportError) as error:
            raise TransientLLMError(f"Gemini: error de red ({error})") from error

        usage = response.usage_metadata
        return LLMResult(
            text=(response.text or "").strip(),
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            model=self.model,
        )


def _translate(error: Any) -> Exception:
    code = int(error.code or 0)
    if code in (401, 403):
        return LLMAuthError(f"Gemini rechazó la API key (HTTP {code}); revisa LLM_API_KEY")
    if code == 429 or code >= 500:
        return TransientLLMError(f"Gemini: HTTP {code}")
    return LLMBadRequestError(f"Gemini rechazó la petición (HTTP {code}): {error.message}")
