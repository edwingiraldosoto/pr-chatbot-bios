"""Adaptador de Google Gemini con el SDK ``google-genai`` (T-23, T-47; CA-R1.4).

``google-genai`` es una dependencia normal: elegir ``LLM_PROVIDER=gemini`` no exige
instalar nada. La temperatura sale de ``LLM_GEMINI_TEMPERATURE`` (vacía = la de fábrica).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMResult,
    LLMTimeoutError,
    TransientLLMError,
)


class GeminiProvider:
    """Llama a ``models.generate_content`` (cliente asíncrono ``client.aio``)."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float | None = None,
        httpx_async_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(timeout_seconds * 1000),  # milisegundos
                # Un solo intento: la política de reintentos es la de RetryingProvider. Sin
                # retry_options el SDK no reintenta con httpx2, pero con aiohttp (si está
                # instalado) reintenta por su cuenta los errores de conexión, con esperas de
                # 1 a 10 s que se sumarían a LLM_TIMEOUT_SECONDS. attempts=1 lo evita.
                retry_options=genai_types.HttpRetryOptions(attempts=1),
                httpx_async_client=httpx_async_client,
            ),
        )

    def request(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Argumentos de ``generate_content`` (Gemini llama ``model`` al asistente).

        ``temperature`` de la llamada reemplaza la del perfil, salvo que el perfil no tenga:
        entonces no se envía y el modelo usa la suya.
        """
        if self.temperature is None:
            temperature = None
        elif temperature is None:
            temperature = self.temperature
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
        max_tokens: int,
        temperature: float | None = None,
    ) -> LLMResult:
        """Genera la respuesta y traduce los errores del SDK."""
        try:
            response = await self._client.aio.models.generate_content(
                **self.request(system, messages, max_tokens=max_tokens, temperature=temperature)
            )
        except genai_errors.APIError as error:
            raise _translate(error) from error
        except (TimeoutError, httpx2.TimeoutException) as error:
            raise LLMTimeoutError("Gemini no respondió a tiempo") from error
        except (OSError, httpx2.TransportError) as error:
            raise TransientLLMError(f"Gemini: error de red ({error})") from error

        usage = response.usage_metadata
        candidate = response.candidates[0] if response.candidates else None
        finish = getattr(candidate.finish_reason, "name", None) if candidate else None
        return LLMResult(
            text=(response.text or "").strip(),
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            model=self.model,
            truncated=finish == "MAX_TOKENS",
        )


def _translate(error: Any) -> Exception:
    code = int(error.code or 0)
    if code in (401, 403):
        return LLMAuthError(
            f"Gemini rechazó la API key (HTTP {code}); revisa LLM_GEMINI_API_KEY o LLM_API_KEY"
        )
    if code == 429 or code >= 500:
        return TransientLLMError(f"Gemini: HTTP {code}")
    return LLMBadRequestError(f"Gemini rechazó la petición (HTTP {code}): {error.message}")
