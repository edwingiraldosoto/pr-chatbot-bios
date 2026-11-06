"""Adaptador de OpenAI y de APIs compatibles (Groq, Ollama, vLLM…) (T-22; CA-R1.4).

Con ``LLM_BASE_URL`` vacío se usa la API oficial; con un valor, cualquier servidor que
implemente ``/chat/completions``. La API oficial pide ``max_completion_tokens`` (los modelos
de razonamiento rechazan ``max_tokens``); las compatibles suelen aceptar solo ``max_tokens``.
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
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )

    def request(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Argumentos de ``chat.completions.create`` (separado para poder probarlo)."""
        limit = "max_tokens" if self.base_url else "max_completion_tokens"
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            "temperature": temperature,
            limit: max_tokens,
        }

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
            response = await self._client.chat.completions.create(
                **self.request(system, messages, temperature=temperature, max_tokens=max_tokens)
            )
        except openai.APITimeoutError as error:
            raise LLMTimeoutError("OpenAI no respondió a tiempo") from error
        except openai.APIConnectionError as error:
            raise TransientLLMError(f"OpenAI: error de red ({error})") from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise LLMAuthError(
                f"OpenAI rechazó la API key (HTTP {error.status_code}); revisa LLM_API_KEY"
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
