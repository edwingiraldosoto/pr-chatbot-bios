"""Interfaz común de los proveedores de LLM, errores unificados y reintentos (RNF-01, RNF-05).

Cada adaptador (Anthropic, OpenAI, Gemini) implementa :class:`LLMProvider` y traduce las
excepciones de su SDK a las de este módulo. :class:`RetryingProvider` envuelve a cualquier
adaptador con una sola política de tiempo y reintentos, probada sin red:

* ``LLM_TIMEOUT_SECONDS`` es el tiempo **total** que espera el usuario, reintentos
  incluidos (:class:`LLMTimeoutError`).
* Los errores transitorios (:class:`TransientLLMError`: 429, 5xx, red) se reintentan hasta
  ``LLM_MAX_RETRIES`` veces con espera exponencial; al agotarlos se lanza
  :class:`LLMUnavailableError`.
* Los errores de autenticación y de petición no se reintentan.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from mesa_ayuda.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ChatMessage:
    """Un turno de la conversación enviado al modelo."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResult:
    """Texto generado y consumo de tokens de una llamada."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMError(Exception):
    """Base de los errores de proveedor que el bot sabe mostrar al usuario."""


class LLMTimeoutError(LLMError):
    """Se superó ``LLM_TIMEOUT_SECONDS``."""


class LLMUnavailableError(LLMError):
    """El servicio no respondió después de los reintentos (429, 5xx o red)."""


class LLMAuthError(LLMError):
    """API key inválida o sin permisos (401/403)."""


class LLMBadRequestError(LLMError):
    """Petición rechazada: modelo inexistente, parámetros inválidos (400/404)."""


class TransientLLMError(LLMError):
    """Error que puede resolverse solo (429, 5xx, red). Lo lanzan los adaptadores.

    :class:`RetryingProvider` lo reintenta y, si persiste, lo convierte en
    :class:`LLMUnavailableError`; nunca llega al bot.
    """


class LLMProvider(Protocol):
    """Lo que el resto del sistema necesita de un modelo de lenguaje."""

    name: str
    model: str

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        """Genera la respuesta del modelo para ``system`` + ``messages``."""
        ...


Sleep = Callable[[float], Awaitable[None]]


class RetryingProvider:
    """Aplica tiempo total y reintentos con espera exponencial a un adaptador.

    Args:
        inner: Adaptador que hace la llamada real (sin reintentos propios).
        timeout_seconds: Tiempo total máximo de :meth:`complete`, reintentos incluidos.
        max_retries: Reintentos ante :class:`TransientLLMError`.
        base_delay: Espera antes del primer reintento; se duplica en cada uno.
        sleep: Función de espera (las pruebas la reemplazan para no esperar).
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        timeout_seconds: float,
        max_retries: int,
        base_delay: float = 0.5,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        """Llama al adaptador; ver la política en la documentación del módulo."""
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._with_retries(
                    system, messages, temperature=temperature, max_tokens=max_tokens
                )
        except TimeoutError as error:
            log.warning("llm_timeout", provider=self.name, timeout_seconds=self.timeout_seconds)
            raise LLMTimeoutError(
                f"{self.name} no respondió en {self.timeout_seconds:g} s"
            ) from error

    async def _with_retries(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        attempt = 0
        while True:
            try:
                return await self._inner.complete(
                    system, messages, temperature=temperature, max_tokens=max_tokens
                )
            except TransientLLMError as error:
                if attempt >= self.max_retries:
                    log.error(
                        "llm_unavailable",
                        provider=self.name,
                        attempts=attempt + 1,
                        error=str(error),
                    )
                    raise LLMUnavailableError(str(error)) from error
                delay = self._base_delay * 2**attempt * random.uniform(0.8, 1.2)
                attempt += 1
                log.warning(
                    "llm_retry",
                    provider=self.name,
                    attempt=attempt,
                    delay_s=round(delay, 2),
                    error=str(error),
                )
                await self._sleep(delay)
            except LLMAuthError as error:
                log.error("llm_auth_error", provider=self.name, error=str(error))
                raise
            except LLMBadRequestError as error:
                log.error("llm_bad_request", provider=self.name, error=str(error))
                raise
