"""Saludos, agradecimientos y despedidas (CA-06.5).

Un mensaje que solo dice "hola" no es una pregunta sobre los documentos: buscarlo daría
"No encontré esa información", que suena brusco. Se reconoce aquí y se contesta con un
texto fijo, sin modelo.

Solo cuenta si el mensaje **completo** es una de las fórmulas de abajo, ignorando tildes,
mayúsculas, signos y letras repetidas ("¡Holaaa!" = "hola"). Si trae algo más ("hola,
¿cuántos días de vacaciones tengo?"), es una pregunta y sigue el flujo normal.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class SmallTalk(StrEnum):
    """Tipo de mensaje social."""

    GREETING = "greeting"
    THANKS = "thanks"
    FAREWELL = "farewell"


_GREETINGS = {
    "hola",
    "holi",
    "holis",
    "holas",
    "hello",
    "hi",
    "hey",
    "buenas",
    "buen dia",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "que tal",
    "como estas",
    "como esta",
    "como vas",
    "que mas",
}
_GREETING_PREFIXES = ("hola", "hey", "buenas", "buenos dias", "buen dia")
_THANKS = {
    "gracias",
    "muchas gracias",
    "mil gracias",
    "gracias de nuevo",
    "ok gracias",
    "listo gracias",
    "perfecto gracias",
    "excelente gracias",
    "genial gracias",
    "thanks",
    "thank you",
}
_FAREWELLS = {
    "chao",
    "chau",
    "adios",
    "bye",
    "hasta luego",
    "hasta pronto",
    "hasta manana",
    "nos vemos",
}

_REPEATED = re.compile(r"(\w)\1+")
_NOT_WORD = re.compile(r"[^\w\s]")


def _normalize(text: str) -> str:
    """Sin tildes, mayúsculas, signos ni letras repetidas; espacios simples."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    plain = _NOT_WORD.sub(" ", plain)
    plain = _REPEATED.sub(r"\1", plain)
    return " ".join(plain.split())


# Las fórmulas pasan por la misma normalización (por ejemplo, "llamar" → "lamar").
_GREETINGS_N = {_normalize(item) for item in _GREETINGS}
_THANKS_N = {_normalize(item) for item in _THANKS}
_FAREWELLS_N = {_normalize(item) for item in _FAREWELLS}
_PREFIXES_N = tuple(_normalize(item) for item in _GREETING_PREFIXES)


def classify(text: str) -> SmallTalk | None:
    """Tipo de mensaje social, o ``None`` si es (o contiene) una pregunta."""
    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in _GREETINGS_N:
        return SmallTalk.GREETING
    # "hola buenas tardes", "hola que tal", "buenas como estas": saludo + saludo.
    for prefix in _PREFIXES_N:
        if normalized.startswith(prefix + " ") and normalized[len(prefix) + 1 :] in _GREETINGS_N:
            return SmallTalk.GREETING
    if normalized in _THANKS_N:
        return SmallTalk.THANKS
    if normalized in _FAREWELLS_N:
        return SmallTalk.FAREWELL
    return None
