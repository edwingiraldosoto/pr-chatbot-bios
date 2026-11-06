"""Construcción del prompt enviado al LLM (RF-03, RNF-04; ``specs/contracts/prompt.md``).

El mensaje final del usuario lleva los fragmentos numerados dentro de ``<documentos>`` y la
pregunta dentro de ``<pregunta>``. Todo texto que viene de un documento o de un usuario se
escapa (``&``, ``<``, ``>`` y comillas en atributos), de modo que nadie puede cerrar esas
etiquetas ni abrir otras: el modelo recibe esos textos como datos (CA-R4.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mesa_ayuda.llm.base import ChatMessage
from mesa_ayuda.vectorstore import SearchHit


@dataclass(frozen=True)
class Prompt:
    """Lo que recibe :meth:`LLMProvider.complete`."""

    system: str
    messages: list[ChatMessage]


def escape_text(text: str) -> str:
    """Escapa ``&``, ``<`` y ``>`` para que el texto no pueda formar etiquetas."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attribute(text: str) -> str:
    """Escapa además las comillas, para valores dentro de ``atributo="…"``."""
    return escape_text(text).replace('"', "&quot;").replace("\n", " ")


def render_documents(hits: Sequence[SearchHit]) -> str:
    """Numera los fragmentos desde 1 en el orden recibido (de mayor a menor similitud)."""
    blocks = []
    for number, hit in enumerate(hits, start=1):
        page = str(hit.page) if hit.page else ""
        blocks.append(
            f'<fragmento id="{number}" fuente="{escape_attribute(hit.source)}" '
            f'seccion="{escape_attribute(hit.section)}" pagina="{page}">\n'
            f"{escape_text(hit.text.strip())}\n"
            "</fragmento>"
        )
    return "<documentos>\n" + "\n".join(blocks) + "\n</documentos>"


def render_question(question: str) -> str:
    """Envuelve la pregunta del usuario en ``<pregunta>``."""
    return f"<pregunta>\n{escape_text(question.strip())}\n</pregunta>"


def normalize_history(history: Sequence[ChatMessage]) -> list[ChatMessage]:
    """Deja el historial listo para la API: empieza con ``user`` y sin turnos vacíos.

    El recorte a ``HISTORY_LENGTH`` puede dejar una respuesta del asistente al principio;
    las API de Anthropic y Gemini exigen que la conversación empiece con el usuario.
    """
    turns = [turn for turn in history if turn.content.strip()]
    while turns and turns[0].role != "user":
        turns.pop(0)
    return turns


def build_answer_prompt(
    system_prompt: str,
    question: str,
    hits: Sequence[SearchHit],
    history: Sequence[ChatMessage] = (),
) -> Prompt:
    """Prompt de la llamada de respuesta: historial + fragmentos + pregunta."""
    final = ChatMessage("user", f"{render_documents(hits)}\n\n{render_question(question)}")
    return Prompt(system=system_prompt, messages=[*normalize_history(history), final])


def build_rewrite_prompt(
    rewrite_prompt: str, question: str, history: Sequence[ChatMessage]
) -> Prompt:
    """Prompt de la reformulación: historial + la pregunta de seguimiento."""
    final = ChatMessage("user", render_question(question))
    return Prompt(system=rewrite_prompt, messages=[*normalize_history(history), final])
