"""Revisión de texto sospechoso en los documentos al indexar (RF-13, RNF-04).

Un documento de la base de conocimiento puede traer, a propósito o no, frases dirigidas al
asistente ("ignora tus instrucciones anteriores…"). El prompt ya marca los fragmentos como
datos (``specs/contracts/prompt.md``); esta revisión es una segunda capa que avisa al
administrador o deja el documento fuera.

Modos (``ingest.injection_check``):

- ``off``: no se revisa (CA-13.3).
- ``warn``: el documento se indexa y la advertencia sale en el log y en el resumen de
  ``/reindex`` con su página o sección (CA-13.1).
- ``quarantine``: el documento no se indexa (CA-13.2).

Los patrones son deliberadamente específicos (frases que le hablan a un asistente), para no
marcar textos normales de una política. No pretenden detectarlo todo: un atacante con
acceso a la carpeta de documentos puede redactar algo que no coincida. El riesgo residual
está descrito en ``docs/DECISIONES.md``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from mesa_ayuda.ingest.chunker import Chunk

InjectionMode = Literal["off", "warn", "quarantine"]

SNIPPET_CHARS = 80

# Se buscan sobre el texto en minúsculas y sin tildes (ver ``_fold``).
_PATTERNS = [
    # "ignora / olvida / omite las instrucciones (anteriores)"
    r"\b(ignora|ignoren|olvida|olviden|omite|descarta|desobedece)\w*\s+(todas?\s+)?"
    r"(las\s+|tus\s+|sus\s+|estas\s+)?(instrucciones|reglas|indicaciones|directrices|ordenes)\b",
    r"\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+|your\s+)?(previous|prior|above|earlier)"
    r"\s+(instructions|rules|prompts?)\b",
    # pedir el prompt de sistema
    r"\b(revela|muestra|dime|imprime|repite|copia)\w*\s+(tu|el|su|las?)\s+"
    r"(prompt|mensaje de sistema|instrucciones de sistema|instrucciones internas)\b",
    r"\bsystem\s+prompt\b",
    r"\bprompt\s+(de|del)\s+sistema\b",
    # cambiar el rol del asistente
    r"\b(a partir de ahora|desde ahora)\s*,?\s+(eres|seras|actua|actuaras|responde|responderas)\b",
    r"\b(you are now|from now on,? you|act as an?)\b",
    r"\b(nuevas instrucciones|new instructions)\s*:",
    r"\b(modo desarrollador|developer mode|jailbreak)\b",
    # dirigirse al modelo
    r"\b(asistente|modelo|ia|chatbot|bot|assistant)\s*(de ia\s*)?,?\s*"
    r"(debes|tienes que|must|should)"
    r"\s+(ignorar|ignore|revelar|reveal|responder siempre|always answer)\b",
    # imitar los delimitadores del prompt
    r"</?\s*(system|sistema|documentos|fragmento|pregunta|instrucciones)\b[^>]*>",
]
_REGEX = re.compile("|".join(f"(?:{pattern})" for pattern in _PATTERNS))


@dataclass(frozen=True)
class Finding:
    """Un texto sospechoso dentro de un documento."""

    location: str  # "página 3", 'sección "Alcance"' o "" si no hay
    snippet: str

    def describe(self) -> str:
        """Texto para el manifest, el log y el resumen de ``/reindex``."""
        what = f'texto que parece una instrucción al asistente ("{self.snippet}")'
        return f"{self.location}: {what}" if self.location else what


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, con la misma longitud que el original (para citar la frase)."""
    out = []
    for char in text:
        lower = char.lower()
        if len(lower) != 1:  # "İ" → "i̇": se deja igual para no correr las posiciones
            lower = char
        out.append(unicodedata.normalize("NFKD", lower)[0])
    return "".join(out)


def _location(chunk: Chunk) -> str:
    if chunk.page:
        return f"página {chunk.page}"
    if chunk.section:
        return f'sección "{chunk.section}"'
    return ""


def _snippet(text: str, start: int, end: int) -> str:
    phrase = " ".join(text[start:end].split())
    if len(phrase) > SNIPPET_CHARS:
        phrase = phrase[: SNIPPET_CHARS - 1].rstrip() + "…"
    return phrase


def find_suspicious(chunks: Sequence[Chunk]) -> list[Finding]:
    """Frases de manipulación en los fragmentos, sin repetir la misma en el mismo lugar."""
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        folded = _fold(chunk.text)
        for match in _REGEX.finditer(folded):
            finding = Finding(_location(chunk), _snippet(chunk.text, *match.span()))
            key = (finding.location, finding.snippet.casefold())
            if key not in seen:  # el solapamiento repite frases entre fragmentos
                seen.add(key)
                findings.append(finding)
    return findings
