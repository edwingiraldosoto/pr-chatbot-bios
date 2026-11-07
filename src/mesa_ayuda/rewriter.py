"""Reformulación de preguntas de seguimiento (RF-05, CA-05.1).

"¿Y cuántos días son?" no sirve para buscar en el índice. Si hay historial, se le pide al
LLM que la reescriba como una pregunta autónoma ("¿Cuántos días de vacaciones tengo al
año?") y se busca con esa. Si la llamada falla o devuelve algo inservible, se usa la
pregunta original: la reformulación mejora la búsqueda, pero nunca la bloquea.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mesa_ayuda.llm.base import ChatMessage, LLMError, LLMProvider
from mesa_ayuda.logging_setup import get_logger
from mesa_ayuda.prompting import build_rewrite_prompt, normalize_history

log = get_logger(__name__)

# Temperatura pedida para reformular: determinista. Si el modelo activo no acepta
# temperatura (perfil sin temperatura), el adaptador no la envía.
REWRITE_TEMPERATURE = 0.0
_QUOTES = "\"'“”«»"


@dataclass(frozen=True)
class Rewrite:
    """Pregunta que se usará para buscar y consumo de la llamada.

    ``called`` indica si se llamó al LLM (aunque luego se descartara su salida).
    """

    question: str
    rewritten: bool
    input_tokens: int = 0
    output_tokens: int = 0
    called: bool = False


class QuestionRewriter:
    """Convierte preguntas de seguimiento en preguntas autónomas.

    Args:
        provider: Proveedor de LLM (con su política de reintentos).
        prompt: Contenido de ``REWRITE_PROMPT_PATH``.
        max_tokens: ``LLM_REWRITE_MAX_TOKENS``.
    """

    def __init__(self, provider: LLMProvider, prompt: str, *, max_tokens: int = 100) -> None:
        self._provider = provider
        self._prompt = prompt
        self._max_tokens = max_tokens

    async def rewrite(self, question: str, history: Sequence[ChatMessage]) -> Rewrite:
        """Devuelve la pregunta autónoma, o la original si no hay historial o algo falla."""
        if not normalize_history(history):
            return Rewrite(question=question, rewritten=False)

        prompt = build_rewrite_prompt(self._prompt, question, history)
        try:
            result = await self._provider.complete(
                prompt.system,
                prompt.messages,
                max_tokens=self._max_tokens,
                temperature=REWRITE_TEMPERATURE,
            )
        except LLMError as error:
            log.warning("rewrite_failed", error_type=type(error).__name__)
            return Rewrite(question=question, rewritten=False, called=True)

        candidate = _clean(result.text)
        reason = _discard_reason(candidate, question)
        if reason:
            log.warning("rewrite_discarded", reason=reason, length=len(candidate))
            return Rewrite(question, False, result.input_tokens, result.output_tokens, called=True)
        return Rewrite(
            candidate, candidate != question, result.input_tokens, result.output_tokens, called=True
        )


def _discard_reason(candidate: str, question: str) -> str:
    """Por qué no sirve la reformulación (``""`` si sirve).

    Visto en S8: a veces el modelo contesta o pide aclaración ("Formatear ¿qué? Ese mensaje
    no permite…") en lugar de reescribir. Una pregunta reescrita termina en su "?".
    """
    if not candidate:
        return "vacía"
    if len(candidate) > max(300, 3 * len(question)):
        return "demasiado larga"
    _, mark, after = candidate.partition("?")
    if mark and any(char.isalnum() for char in after):
        return "no es una sola pregunta"
    return ""


def _clean(text: str) -> str:
    """Primera línea no vacía, sin comillas ni la etiqueta ``<pregunta>`` alrededor."""
    lines = [
        line.strip()
        for line in text.replace("<pregunta>", "").replace("</pregunta>", "").splitlines()
    ]
    first = next((line for line in lines if line), "")
    return first.strip(_QUOTES).strip()
