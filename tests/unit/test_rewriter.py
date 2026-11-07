"""Pruebas de la reformulación de seguimientos (T-24; RF-05, CA-05.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_ayuda.llm.base import ChatMessage, LLMTimeoutError, LLMUnavailableError
from mesa_ayuda.llm.fake import FakeProvider
from mesa_ayuda.rewriter import QuestionRewriter

HISTORY = [
    ChatMessage("user", "¿Cuántos días de vacaciones tengo?"),
    ChatMessage("assistant", "Tienes 15 días hábiles por año."),
]


async def test_follow_up_is_rewritten_with_the_history() -> None:
    fake = FakeProvider(replies=["¿Se pueden acumular los días de vacaciones?"])

    result = await QuestionRewriter(fake, "REESCRIBE").rewrite("¿y se pueden acumular?", HISTORY)

    assert result.question == "¿Se pueden acumular los días de vacaciones?" and result.rewritten
    call = fake.calls[0]
    assert call.system == "REESCRIBE"
    assert (call.temperature, call.max_tokens) == (0.0, 100)
    assert list(call.messages[:2]) == HISTORY
    assert call.messages[-1].content == "<pregunta>\n¿y se pueden acumular?\n</pregunta>"
    assert result.input_tokens > 0


async def test_rewrite_max_tokens_is_configurable() -> None:
    # LLM_REWRITE_MAX_TOKENS: los modelos que razonan pueden necesitar más de 100.
    fake = FakeProvider(replies=["¿Se acumulan las vacaciones?"])

    await QuestionRewriter(fake, "R", max_tokens=400).rewrite("¿y eso?", HISTORY)

    assert fake.calls[0].max_tokens == 400


async def test_without_history_there_is_no_llm_call() -> None:
    fake = FakeProvider()

    result = await QuestionRewriter(fake, "R").rewrite("¿Cuántos días tengo?", [])

    assert (result.question, result.rewritten) == ("¿Cuántos días tengo?", False)
    assert fake.calls == []


async def test_history_with_only_an_orphan_answer_counts_as_empty() -> None:
    fake = FakeProvider()

    await QuestionRewriter(fake, "R").rewrite("x", [ChatMessage("assistant", "y")])

    assert fake.calls == []


@pytest.mark.parametrize("error", [LLMTimeoutError("t"), LLMUnavailableError("u")])
async def test_llm_failure_falls_back_to_the_original(error: Exception) -> None:
    result = await QuestionRewriter(FakeProvider(replies=[error]), "R").rewrite("¿y eso?", HISTORY)

    assert (result.question, result.rewritten) == ("¿y eso?", False)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('"¿Se acumulan las vacaciones?"', "¿Se acumulan las vacaciones?"),
        ("\n<pregunta>\n¿Se acumulan las vacaciones?\n</pregunta>", "¿Se acumulan las vacaciones?"),
        (
            "¿Se acumulan las vacaciones?\nExplicación: resolví 'eso'.",
            "¿Se acumulan las vacaciones?",
        ),
    ],
)
async def test_output_is_cleaned(reply: str, expected: str) -> None:
    result = await QuestionRewriter(FakeProvider(replies=[reply]), "R").rewrite("¿y eso?", HISTORY)

    assert result.question == expected


@pytest.mark.parametrize("reply", ["", "   ", "x" * 400])
async def test_useless_output_falls_back_to_the_original(reply: str) -> None:
    result = await QuestionRewriter(FakeProvider(replies=[reply]), "R").rewrite("¿y eso?", HISTORY)

    assert (result.question, result.rewritten) == ("¿y eso?", False)


async def test_unchanged_question_is_not_marked_as_rewritten() -> None:
    fake = FakeProvider(replies=["¿Qué es el auxilio de conectividad?"])

    result = await QuestionRewriter(fake, "R").rewrite(
        "¿Qué es el auxilio de conectividad?", HISTORY
    )

    assert not result.rewritten


@pytest.mark.parametrize(
    "reply",
    [
        # Visto en S8: el modelo pidió aclaración en lugar de reescribir.
        "Formatear ¿qué? Ese mensaje no permite reescribir una pregunta autónoma.",
        "¿Qué quieres formatear? Puede ser un equipo o un documento.",
    ],
)
async def test_output_that_is_not_a_single_question_falls_back(reply: str) -> None:
    result = await QuestionRewriter(FakeProvider(replies=[reply]), "R").rewrite(
        "Formatear", HISTORY
    )

    assert (result.question, result.rewritten, result.called) == ("Formatear", False, True)


async def test_question_mark_at_the_end_is_fine() -> None:
    result = await QuestionRewriter(
        FakeProvider(replies=["¿Se pueden acumular las vacaciones?"]), "R"
    ).rewrite("¿y se acumulan?", HISTORY)

    assert result.rewritten


def test_repository_prompt_keeps_unrelated_questions_as_they_are() -> None:
    # Contrato de S8 (specs/contracts/prompt.md): la regla conservadora está en el prompt.
    prompt = (Path(__file__).resolve().parents[2] / "prompts" / "rewrite_prompt.md").read_text(
        encoding="utf-8"
    )

    assert "devuelve la pregunta exactamente igual" in prompt
    assert "Nunca agregues cifras" in prompt
    assert "no pidas aclaraciones" in prompt
