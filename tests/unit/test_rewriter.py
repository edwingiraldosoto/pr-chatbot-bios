"""Pruebas de la reformulación de seguimientos (T-24; RF-05, CA-05.1)."""

from __future__ import annotations

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
