"""Adaptador de Anthropic (Claude) con el SDK oficial ``anthropic`` (T-21, T-47; CA-R1.4).

Parámetros probados contra la API el 2026-11-06 (``specs/research.md``, R-05). Todos salen
de la configuración (CA-R1.6):

* ``temperature`` nunca se envía: el SDK 2.6 la retiró junto con los demás parámetros de
  muestreo.
* ``output_config.effort`` = ``LLM_ANTHROPIC_EFFORT`` (``low`` por defecto): Haiku 5.5
  razona por defecto y esos tokens cuentan dentro de ``max_tokens``; con ``low`` respondió
  sin razonamiento. Vacío = no se envía (Haiku 4.5 y anteriores lo rechazan).
* ``thinking`` = ``{"type": LLM_ANTHROPIC_THINKING}`` (``disabled`` por defecto). El 2026-11-07
  se vio que ``effort = "low"`` no basta: ante una pregunta con varios temas ("Vacaciones,
  extras, equipo") Haiku 5.5 razonó hasta agotar los 600 tokens y la respuesta llegó cortada
  en 2 de 3 intentos; con ``disabled`` respondió completa en 3 de 3 (270-380 tokens).
* ``stop_reason == "max_tokens"`` marca el resultado como ``truncated``.
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
        effort: str | None = "low",
        thinking: str | None = "disabled",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.thinking = thinking
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
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self.thinking:
            params["thinking"] = {"type": self.thinking}
        return params

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
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
                f"Anthropic rechazó la API key (HTTP {error.status_code}); "
                f"revisa LLM_ANTHROPIC_API_KEY o LLM_API_KEY"
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
            truncated=response.stop_reason == "max_tokens",
        )
