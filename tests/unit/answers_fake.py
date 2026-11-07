"""Servicio de respuesta falso para las pruebas del bot: respuestas preparadas por la prueba."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from mesa_ayuda.answering import Answer, Outcome, Source, Usage
from mesa_ayuda.llm.base import ChatMessage

Script = Answer | BaseException | Callable[[str], Awaitable[Answer]]


def answered(text: str, *sources: Source, question: str = "") -> Answer:
    """Respuesta encontrada con sus fuentes."""
    return Answer(
        text=text,
        sources=list(sources) or [Source("politica_vacaciones.md", "Días")],
        outcome=Outcome.ANSWERED,
        usage=Usage(120, 30, 1),
        best_score=0.87,
        search_question=question,
    )


def not_found(
    outcome: Outcome = Outcome.BELOW_THRESHOLD,
    *,
    search_question: str = "",
    best_score: float | None = 0.71,
) -> Answer:
    """Pregunta sin respuesta."""
    return Answer("", [], outcome, Usage(), best_score, search_question)


@dataclass
class ScriptedAnswers:
    """Devuelve (o lanza) lo preparado, en orden; registra cada pregunta con su historial."""

    script: list[Script] = field(default_factory=list)
    calls: list[tuple[str, list[ChatMessage]]] = field(default_factory=list)
    delay: float = 0.0

    async def answer(self, question: str, history: Sequence[ChatMessage] = ()) -> Answer:
        self.calls.append((question, list(history)))
        if self.delay:
            await asyncio.sleep(self.delay)
        step: Script = self.script.pop(0) if self.script else answered("Respuesta.")
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, Answer):
            if not step.search_question:
                return Answer(
                    step.text,
                    step.sources,
                    step.outcome,
                    step.usage,
                    step.best_score,
                    question,
                    step.cited_fragments,
                )
            return step
        return await step(question)
