"""Adaptador de Anthropic (Claude) con el SDK oficial ``anthropic`` (T-21; CA-R1.4).

Parámetros probados contra la API el 2026-11-06 (``specs/research.md``, R-05):

* ``temperature`` no se envía: el SDK 2.6 la retiró junto con los demás parámetros de
  muestreo. ``LLM_TEMPERATURE`` solo aplica a OpenAI y Gemini.
* ``output_config.effort = "low"``: Haiku 5.5 razona por defecto y esos tokens cuentan
  dentro de ``max_tokens``; con ``low`` respondió sin razonamiento. Los modelos que no
  aceptan ``effort`` (Haiku 4.5 y anteriores) lo reciben sin ese parámetro.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMResult,
    LLMTimeoutError,
    TransientLLMError,
)

# Modelos que responden 400 si reciben ``output_config.effort``.
MODELS_WITHOUT_EFFORT = (
    "claude-3",
    "claude-haiku-4",
    "claude-sonnet-4-0",
    "claude-sonnet-4-5",
    "claude-opus-4-0",
    "claude-opus-4-1",
)


def supports_effort(model: str) -> bool:
    """``True`` si el modelo acepta ``output_config.effort``."""
    return not model.startswith(MODELS_WITHOUT_EFFORT)


class AnthropicProvider:
    """Llama a la API de Mensajes de Anthropic.

    Los reintentos del SDK se desactivan (``max_retries=0``): la política de reintentos es
    la de :class:`~mesa_ayuda.llm.base.RetryingProvider`.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )

    def request(
        self, system: str, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> dict[str, Any]:
        """Argumentos de ``messages.create`` (separado para poder probarlo sin red)."""
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if supports_effort(self.model):
            params["output_config"] = {"effort": "low"}
        return params

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        """Genera la respuesta. ``temperature`` se ignora (ver la documentación del módulo)."""
        del temperature
        try:
            response = await self._client.messages.create(
                **self.request(system, messages, max_tokens=max_tokens)
            )
        except anthropic.APITimeoutError as error:
            raise LLMTimeoutError("Anthropic no respondió a tiempo") from error
        except anthropic.APIConnectionError as error:
            raise TransientLLMError(f"Anthropic: error de red ({error})") from error
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            raise LLMAuthError(
                f"Anthropic rechazó la API key (HTTP {error.status_code}); revisa LLM_API_KEY"
            ) from error
        except (anthropic.BadRequestError, anthropic.NotFoundError) as error:
            raise LLMBadRequestError(
                f"Anthropic rechazó la petición (HTTP {error.status_code}): {error.message}"
            ) from error
        except anthropic.APIStatusError as error:
            if error.status_code == 429 or error.status_code >= 500:
                raise TransientLLMError(f"Anthropic: HTTP {error.status_code}") from error
            raise LLMBadRequestError(
                f"Anthropic: HTTP {error.status_code}: {error.message}"
            ) from error

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text.strip(),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )
