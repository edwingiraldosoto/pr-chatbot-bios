"""Adaptador de OpenAI y de APIs compatibles (Groq, Ollama, vLLM…) (T-22, T-47; CA-R1.4).

Con ``LLM_OPENAI_BASE_URL`` vacío se usa la API oficial; con un valor, cualquier servidor
que implemente ``/chat/completions``. Todo lo que cambia entre modelos sale de la
configuración (CA-R1.6):

* ``temperature`` (``LLM_OPENAI_TEMPERATURE``): vacía = no se envía; los GPT-5 solo aceptan
  la de fábrica.
* ``reasoning_effort`` (``LLM_OPENAI_REASONING_EFFORT``): vacío para modelos sin
  razonamiento.
* Límite de salida (``LLM_OPENAI_TOKEN_PARAM``): ``auto`` usa ``max_completion_tokens`` con
  la API oficial (los modelos de razonamiento rechazan ``max_tokens``) y ``max_tokens`` con
  una URL propia, que es lo que suelen aceptar las APIs compatibles.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import openai

from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMResult,
    LLMTimeoutError,
    TransientLLMError,
)


class OpenAIProvider:
    """Llama a ``chat.completions`` con los reintentos del SDK desactivados."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        base_url: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        token_param: str = "auto",
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        if token_param == "auto":
            token_param = "max_tokens" if base_url else "max_completion_tokens"
        self.token_param = token_param
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )

    def request(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Argumentos de ``chat.completions.create`` (separado para poder probarlo).

        ``temperature`` de la llamada reemplaza la del perfil, salvo que el perfil no tenga
        temperatura: entonces el modelo no la acepta y no se envía.
        """
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            self.token_param: max_tokens,
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature if temperature is None else temperature
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        return params

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
            response = await self._client.chat.completions.create(
                **self.request(system, messages, max_tokens=max_tokens, temperature=temperature)
            )
        except openai.APITimeoutError as error:
            raise LLMTimeoutError("OpenAI no respondió a tiempo") from error
        except openai.APIConnectionError as error:
            raise TransientLLMError(f"OpenAI: error de red ({error})") from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise LLMAuthError(
                f"OpenAI rechazó la API key (HTTP {error.status_code}); "
                f"revisa LLM_OPENAI_API_KEY o LLM_API_KEY"
            ) from error
        except (openai.BadRequestError, openai.NotFoundError) as error:
            raise LLMBadRequestError(
                f"OpenAI rechazó la petición (HTTP {error.status_code}): {error.message}"
            ) from error
        except openai.APIStatusError as error:
            if error.status_code == 429 or error.status_code >= 500:
                raise TransientLLMError(f"OpenAI: HTTP {error.status_code}") from error
            raise LLMBadRequestError(
                f"OpenAI: HTTP {error.status_code}: {error.message}"
            ) from error

        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice else ""
        usage = response.usage
        return LLMResult(
            text=text.strip(),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model or self.model,
        )
