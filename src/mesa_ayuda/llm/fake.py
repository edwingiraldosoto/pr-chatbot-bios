"""Proveedor falso para pruebas: respuestas programadas y registro de llamadas (principio V)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from mesa_ayuda.llm.base import ChatMessage, LLMResult

Reply = str | LLMResult | BaseException | Callable[[str, Sequence[ChatMessage]], str]


@dataclass(frozen=True)
class FakeCall:
    """Lo que recibió el proveedor en una llamada."""

    system: str
    messages: tuple[ChatMessage, ...]
    temperature: float | None
    max_tokens: int


@dataclass
class FakeProvider:
    """Devuelve, en orden, las respuestas de ``replies``.

    Cada respuesta puede ser un texto, un :class:`LLMResult` (se devuelve tal cual), una
    excepción (se lanza) o una función
    ``(system, messages) -> texto``. Cuando se acaban, se repite ``default``.

    Attributes:
        calls: Llamadas recibidas, para verificar el prompt en las pruebas.
        delay: Segundos de espera simulada antes de responder.
    """

    replies: list[Reply] = field(default_factory=list)
    default: Reply = "NO_ENCONTRADO"
    delay: float = 0.0
    name: str = "fake"
    model: str = "fake-model"
    calls: list[FakeCall] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> LLMResult:
        """Registra la llamada y devuelve la siguiente respuesta programada."""
        self.calls.append(FakeCall(system, tuple(messages), temperature, max_tokens))
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if self.replies else self.default
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, LLMResult):
            return reply
        text = reply(system, messages) if callable(reply) else reply
        input_tokens = (len(system) + sum(len(m.content) for m in messages)) // 4
        return LLMResult(
            text=text, input_tokens=input_tokens, output_tokens=len(text) // 4, model=self.model
        )
