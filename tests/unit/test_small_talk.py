"""Pruebas del reconocimiento de saludos, agradecimientos y despedidas (T-50; CA-06.5)."""

from __future__ import annotations

import pytest

from mesa_ayuda.bot.small_talk import SmallTalk, classify


@pytest.mark.parametrize(
    "text",
    [
        "hola",
        "Hola",
        "HOLA!!",
        "¡Hola!",
        "holaaa",
        "Holaas",
        "  hola  ",
        "holi",
        "buenas",
        "Buenos días",
        "buenos dias",
        "Buen día",
        "buenas tardes",
        "Buenas noches.",
        "hola, buenas tardes",
        "Hola buenos días",
        "hola que tal",
        "¿Qué tal?",
        "hola, ¿cómo estás?",
        "hey",
        "saludos",
    ],
)
def test_greetings(text: str) -> None:
    assert classify(text) is SmallTalk.GREETING


@pytest.mark.parametrize(
    "text", ["gracias", "Gracias!", "muchas gracias", "Mil gracias", "ok, gracias", "graciaaas"]
)
def test_thanks(text: str) -> None:
    assert classify(text) is SmallTalk.THANKS


@pytest.mark.parametrize("text", ["chao", "Chau", "adiós", "hasta luego", "Nos vemos", "bye"])
def test_farewells(text: str) -> None:
    assert classify(text) is SmallTalk.FAREWELL


@pytest.mark.parametrize(
    "text",
    [
        "hola, ¿cuántos días de vacaciones tengo?",
        "buenos días, necesito ayuda con la VPN",
        "gracias, ¿y cómo pido el reembolso?",
        "¿Cuántos días de vacaciones tengo?",
        "hola mundo",
        "vacaciones",
        "chao, una última pregunta: ¿hay teletrabajo?",
        "",
        "!!!",
        "😀",
    ],
)
def test_anything_more_than_a_formula_is_not_small_talk(text: str) -> None:
    assert classify(text) is None
