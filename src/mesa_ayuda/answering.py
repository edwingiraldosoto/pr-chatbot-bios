"""Servicio de respuesta: el flujo completo de una pregunta (RF-03, RF-04, RF-05).

1. Si hay historial, la pregunta se reformula para buscar (:mod:`mesa_ayuda.rewriter`).
2. Se recuperan los fragmentos. Si ninguno alcanza el umbral, se responde "no encontrado"
   **sin llamar al LLM** (CA-04.1).
3. Se llama al LLM con los fragmentos relevantes numerados. Si responde ``NO_ENCONTRADO``,
   se responde "no encontrado" sin fuentes (CA-04.2).
4. Se leen los marcadores ``[n]`` de la respuesta: se quitan del texto y las fuentes se
   construyen con los metadatos del índice de esos fragmentos, nunca con nombres escritos
   por el modelo (CA-03.1–CA-03.3).
5. Si el modelo se detuvo por ``LLM_MAX_TOKENS``, se repite la llamada una vez con
   ``LLM_TRUNCATED_RETRY_MAX_TOKENS`` para que la respuesta salga completa (CA-R5.8). Si
   también se corta, se recorta a la última oración completa (o, si no hay ninguna, a la
   última palabra completa) y se avisa al usuario; nunca termina a mitad de una palabra.

Los errores del proveedor (:class:`~mesa_ayuda.llm.base.LLMError`) se propagan: el bot los
convierte en mensajes para el usuario (RNF-05).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from mesa_ayuda.llm.base import ChatMessage, LLMProvider
from mesa_ayuda.logging_setup import get_logger
from mesa_ayuda.prompting import build_answer_prompt
from mesa_ayuda.retrieval import Retriever
from mesa_ayuda.rewriter import QuestionRewriter
from mesa_ayuda.vectorstore import SearchHit

log = get_logger(__name__)

NOT_FOUND_TOKEN = "NO_ENCONTRADO"
_MARKER = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)*)\s*\]")
_MARKER_WITH_SPACE = re.compile(r"[ \t]*" + _MARKER.pattern)
_SPACES = re.compile(r"[ \t]{2,}")
# "p. m. [1]." queda como "p. m.." al quitar el marcador; los puntos suspensivos se respetan.
_DOUBLE_PERIOD = re.compile(r"(?<!\.)\.\.(?!\.)")
_NOT_LETTERS = re.compile(r"[^A-Z_]")
# Fin de una oración completa: punto, cierre de pregunta o exclamación (con su marcador [n]).
_SENTENCE_END = re.compile(r"[.?!](?:\s*\[[\d,;\s]+\])*(?=\s|$)")

TRUNCATED_NOTE = (
    "La respuesta quedó incompleta. Pregúntame por un tema a la vez para darte el detalle."
)


class Outcome(StrEnum):
    """Cómo terminó una pregunta. Los dos "no encontrado" se registran en RF-10."""

    ANSWERED = "answered"
    BELOW_THRESHOLD = "below_threshold"
    LLM_NOT_FOUND = "llm_not_found"


@dataclass(frozen=True)
class Source:
    """Documento citado, con la ubicación que tenga (sección, página o ninguna)."""

    source: str
    section: str = ""
    page: int = 0

    @classmethod
    def from_hit(cls, hit: SearchHit) -> Source:
        """Fuente a partir de los metadatos del índice."""
        return cls(source=hit.source, section=hit.section, page=hit.page)

    def label(self) -> str:
        """``politica.md, sección "2. Días"`` o ``reembolso.pdf, página 3``."""
        parts = [self.source]
        if self.section:
            parts.append(f'sección "{self.section}"')
        if self.page:
            parts.append(f"página {self.page}")
        return ", ".join(parts)


def format_sources(sources: Sequence[Source]) -> str:
    """Línea de fuentes en texto plano (``specs/contracts/prompt.md``)."""
    if not sources:
        return ""
    return "Fuentes: " + "; ".join(source.label() for source in sources) + "."


@dataclass(frozen=True)
class Usage:
    """Tokens consumidos por todas las llamadas al LLM de una pregunta."""

    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0


@dataclass(frozen=True)
class Answer:
    """Resultado de :meth:`AnswerService.answer`.

    Attributes:
        text: Respuesta sin marcadores ni línea de fuentes (vacía si no se encontró).
        sources: Fuentes citadas, sin duplicados, en orden de aparición.
        outcome: Cómo terminó la pregunta.
        usage: Consumo de tokens.
        best_score: Mejor similitud recuperada (``None`` si el índice está vacío).
        search_question: Pregunta con la que se buscó (la reformulada, si la hubo).
        cited_fragments: Números de fragmento que citó el modelo.
        truncated: El modelo se detuvo por ``LLM_MAX_TOKENS`` y ``text`` se recortó a la
            última oración completa.
    """

    text: str
    sources: list[Source]
    outcome: Outcome
    usage: Usage
    best_score: float | None
    search_question: str
    cited_fragments: list[int] = field(default_factory=list)
    truncated: bool = False

    @property
    def found(self) -> bool:
        """``True`` si hay una respuesta con contenido."""
        return self.outcome is Outcome.ANSWERED

    def render(self) -> str:
        """Texto para el usuario: respuesta, aviso de recorte y, separada, la línea de fuentes.

        El aviso de recorte va aquí y no en ``text`` porque ``text`` es lo que se guarda en
        el historial: el aviso no debe volver al modelo en la siguiente pregunta.
        """
        parts = [self.text]
        if self.truncated:
            parts.append(TRUNCATED_NOTE)
        if self.sources:
            parts.append(format_sources(self.sources))
        return "\n\n".join(parts)


def is_not_found(text: str) -> bool:
    """``True`` si el modelo indicó que los fragmentos no contienen la respuesta.

    Acepta ``NO_ENCONTRADO`` solo o al inicio, ignorando espacios, puntuación y
    mayúsculas: ``"No_encontrado."`` o ``NO_ENCONTRADO`` seguido de una explicación.
    """
    words = text.strip().split(maxsplit=1)
    return bool(words) and _NOT_LETTERS.sub("", words[0].upper()) == NOT_FOUND_TOKEN


@dataclass(frozen=True)
class Citations:
    """Texto sin marcadores y números de fragmento citados."""

    text: str
    used: list[int]
    invalid: list[int]


def extract_citations(text: str, fragment_count: int) -> Citations:
    """Quita los marcadores ``[n]`` (también ``[1, 2]``) y valida sus números."""
    used: list[int] = []
    invalid: list[int] = []
    for match in _MARKER.finditer(text):
        for number in (int(n) for n in re.split(r"[,;]", match.group(1))):
            target = used if 1 <= number <= fragment_count else invalid
            if number not in target:
                target.append(number)
    clean = _DOUBLE_PERIOD.sub(".", _MARKER_WITH_SPACE.sub("", text))
    clean = "\n".join(_SPACES.sub(" ", line).rstrip() for line in clean.splitlines())
    return Citations(text=clean.strip(), used=used, invalid=invalid)


def complete_sentences(text: str) -> str:
    """Texto hasta la última oración completa (``""`` si no hay ninguna)."""
    ends = [match.end() for match in _SENTENCE_END.finditer(text)]
    return text[: ends[-1]].rstrip() if ends else ""


def complete_words(text: str) -> str:
    """Texto sin la última palabra (cortada), terminado en ``…`` (``""`` si no queda nada)."""
    head, _, _ = text.rstrip().rpartition(" ")
    head = head.rstrip(" ,;:-–—")
    return f"{head}…" if head else ""


def trim_truncated(text: str) -> str:
    """Respuesta cortada sin la parte incompleta: oraciones completas o, si no hay, palabras."""
    return complete_sentences(text) or complete_words(text)


def unique_sources(hits: Sequence[SearchHit]) -> list[Source]:
    """Fuentes de los fragmentos, sin repetir, conservando el orden."""
    return list(dict.fromkeys(Source.from_hit(hit) for hit in hits))


class AnswerService:
    """Orquesta reformulación, recuperación, llamada al LLM y citas.

    Args:
        retriever: Búsqueda con umbral.
        provider: Proveedor de LLM (con su política de tiempo y reintentos).
        rewriter: Reformulador de seguimientos.
        system_prompt: Contenido de ``SYSTEM_PROMPT_PATH``.
        max_tokens: ``LLM_MAX_TOKENS``. La temperatura y los demás parámetros del modelo
            son del perfil del proveedor (:attr:`Settings.llm`).
        retry_max_tokens: ``LLM_TRUNCATED_RETRY_MAX_TOKENS``: límite del único reintento
            cuando la respuesta se corta por ``max_tokens``. 0 (o un valor no mayor que
            ``max_tokens``) desactiva el reintento.
    """

    def __init__(
        self,
        *,
        retriever: Retriever,
        provider: LLMProvider,
        rewriter: QuestionRewriter,
        system_prompt: str,
        max_tokens: int,
        retry_max_tokens: int = 0,
    ) -> None:
        self._retriever = retriever
        self._provider = provider
        self._rewriter = rewriter
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._retry_max_tokens = retry_max_tokens

    async def answer(self, question: str, history: Sequence[ChatMessage] = ()) -> Answer:
        """Responde una pregunta con fuentes, o indica que no se encontró."""
        start = time.perf_counter()
        rewrite = await self._rewriter.rewrite(question, history)
        usage = Usage(rewrite.input_tokens, rewrite.output_tokens, int(rewrite.called))
        retrieval = await asyncio.to_thread(self._retriever.retrieve, rewrite.question)

        def finish(answer: Answer, **extra: object) -> Answer:
            log.info(
                "answer_outcome",
                outcome=answer.outcome.value,
                best_score=round(answer.best_score, 4) if answer.best_score is not None else None,
                relevant=len(retrieval.relevant),
                fragments=[
                    f"{hit.source}#{hit.chunk_index}={hit.score:.3f}" for hit in retrieval.relevant
                ],
                rewritten=rewrite.rewritten,
                cited=answer.cited_fragments,
                input_tokens=answer.usage.input_tokens,
                output_tokens=answer.usage.output_tokens,
                latency_ms=round((time.perf_counter() - start) * 1000),
                **extra,
            )
            return answer

        not_found = Answer(
            text="",
            sources=[],
            outcome=Outcome.BELOW_THRESHOLD,
            usage=usage,
            best_score=retrieval.best_score,
            search_question=rewrite.question,
        )
        if not retrieval.found:
            return finish(not_found, threshold=retrieval.threshold)

        hits = retrieval.relevant
        prompt = build_answer_prompt(self._system_prompt, question, hits, history)
        limit = self._max_tokens
        result = await self._provider.complete(prompt.system, prompt.messages, max_tokens=limit)
        usage = Usage(
            usage.input_tokens + result.input_tokens,
            usage.output_tokens + result.output_tokens,
            usage.llm_calls + 1,
        )
        if result.truncated and self._retry_max_tokens > self._max_tokens:
            # Una respuesta cortada es peor que una llamada más: se repite una sola vez con
            # más margen, así el costo del peor caso sigue acotado (CA-R5.8).
            log.warning(
                "answer_truncated_retry",
                output_tokens=result.output_tokens,
                max_tokens=limit,
                retry_max_tokens=self._retry_max_tokens,
            )
            limit = self._retry_max_tokens
            result = await self._provider.complete(prompt.system, prompt.messages, max_tokens=limit)
            usage = Usage(
                usage.input_tokens + result.input_tokens,
                usage.output_tokens + result.output_tokens,
                usage.llm_calls + 1,
            )

        text = result.text
        if result.truncated:
            text = trim_truncated(text)
            log.warning(
                "answer_truncated",
                output_tokens=result.output_tokens,
                max_tokens=limit,
                kept_chars=len(text),
            )
        citations = extract_citations(text, len(hits))
        if is_not_found(text) or not citations.text:
            log.info("llm_not_found", empty=not citations.text)
            return finish(
                Answer("", [], Outcome.LLM_NOT_FOUND, usage, retrieval.best_score, rewrite.question)
            )

        if citations.invalid:
            log.warning("citation_invalid", markers=citations.invalid, fragments=len(hits))
        if citations.used:
            cited_hits = [hits[number - 1] for number in citations.used]
        else:
            log.warning("citations_fallback", fragments=len(hits))
            cited_hits = list(hits)
        return finish(
            Answer(
                text=citations.text,
                sources=unique_sources(cited_hits),
                outcome=Outcome.ANSWERED,
                usage=usage,
                best_score=retrieval.best_score,
                search_question=rewrite.question,
                cited_fragments=citations.used,
                truncated=result.truncated,
            )
        )
