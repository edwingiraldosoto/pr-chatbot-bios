"""Pruebas del servicio de respuesta (T-25; RF-03, RF-04, CA-03.1–CA-03.3, CA-04.1, CA-04.2)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
import structlog

from mesa_ayuda.answering import (
    AnswerService,
    Outcome,
    Source,
    extract_citations,
    format_sources,
    is_not_found,
)
from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.llm.base import ChatMessage, LLMUnavailableError
from mesa_ayuda.llm.fake import FakeProvider
from mesa_ayuda.retrieval import Retriever
from mesa_ayuda.rewriter import QuestionRewriter
from mesa_ayuda.vectorstore import SearchHit

VACATIONS = SearchHit(
    text="2. Días de vacaciones\nCada colaborador tiene derecho a 15 días hábiles.",
    source="politica_vacaciones.md",
    section="2. Días de vacaciones",
    page=0,
    chunk_index=1,
    doc_key="politica_vacaciones.md@1",
    score=0.90,
)
ACCUMULATION = SearchHit(
    text="4. Acumulación\nSe pueden acumular hasta dos periodos.",
    source="politica_vacaciones.md",
    section="4. Acumulación",
    page=0,
    chunk_index=3,
    doc_key="politica_vacaciones.md@1",
    score=0.86,
)
REFUND = SearchHit(
    text="Paso 3: Envía la solicitud a más tardar 30 días.",
    source="reembolso_gastos.pdf",
    section="",
    page=3,
    chunk_index=5,
    doc_key="reembolso_gastos.pdf@1",
    score=0.84,
)
WEAK = SearchHit(
    text="El auxilio de conectividad es de $85.000.",
    source="trabajo_remoto.txt",
    section="",
    page=0,
    chunk_index=0,
    doc_key="trabajo_remoto.txt@1",
    score=0.70,
)


class StaticSearch:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries = 0

    def __call__(self, embedding: Sequence[float], top_k: int) -> list[SearchHit]:
        self.queries += 1
        return self.hits[:top_k]


def service(fake: FakeProvider, hits: list[SearchHit], *, threshold: float = 0.80) -> AnswerService:
    retriever = Retriever(FakeEmbedder(), StaticSearch(hits), top_k=5, threshold=threshold)
    return AnswerService(
        retriever=retriever,
        provider=fake,
        rewriter=QuestionRewriter(fake, "REESCRIBE"),
        system_prompt="SISTEMA",
        max_tokens=600,
    )


async def test_answer_cites_only_the_fragments_the_model_used() -> None:
    # CA-03.2: se citan los fragmentos marcados, no todos los recuperados.
    fake = FakeProvider(replies=["Tienes 15 días hábiles [1]. Envía la solicitud en 30 días [3]."])

    answer = await service(fake, [VACATIONS, ACCUMULATION, REFUND, WEAK]).answer("¿Vacaciones?")

    assert answer.outcome is Outcome.ANSWERED and answer.found
    assert answer.text == "Tienes 15 días hábiles. Envía la solicitud en 30 días."
    assert answer.sources == [
        Source("politica_vacaciones.md", "2. Días de vacaciones"),
        Source("reembolso_gastos.pdf", page=3),
    ]
    assert answer.cited_fragments == [1, 3]
    assert answer.render() == (
        "Tienes 15 días hábiles. Envía la solicitud en 30 días.\n\n"
        'Fuentes: politica_vacaciones.md, sección "2. Días de vacaciones"; '
        "reembolso_gastos.pdf, página 3."
    )


async def test_only_relevant_fragments_reach_the_prompt() -> None:
    fake = FakeProvider(replies=["Sí [1]."])

    await service(fake, [VACATIONS, WEAK]).answer("¿Vacaciones?")

    [call] = fake.calls
    content = call.messages[-1].content
    assert "politica_vacaciones.md" in content and "trabajo_remoto.txt" not in content
    assert (call.system, call.temperature, call.max_tokens) == ("SISTEMA", None, 600)


async def test_below_threshold_does_not_call_the_llm() -> None:
    # CA-04.1
    fake = FakeProvider(replies=["no debería llamarse"])

    answer = await service(fake, [WEAK]).answer("¿Cuál es la capital de Australia?")

    assert answer.outcome is Outcome.BELOW_THRESHOLD and not answer.found
    assert (answer.text, answer.sources, answer.render()) == ("", [], "")
    assert answer.best_score == pytest.approx(0.70)
    assert fake.calls == [] and answer.usage.llm_calls == 0


async def test_empty_index_is_not_found() -> None:
    answer = await service(FakeProvider(), []).answer("¿Algo?")

    assert answer.outcome is Outcome.BELOW_THRESHOLD and answer.best_score is None


@pytest.mark.parametrize(
    "reply", ["NO_ENCONTRADO", "  no_encontrado. ", "NO_ENCONTRADO\nNo aparece."]
)
async def test_model_not_found_has_no_sources(reply: str) -> None:
    # CA-04.2
    answer = await service(FakeProvider(replies=[reply]), [VACATIONS]).answer("¿Bono navideño?")

    assert answer.outcome is Outcome.LLM_NOT_FOUND
    assert answer.sources == [] and answer.render() == ""


async def test_answer_of_only_markers_counts_as_not_found() -> None:
    answer = await service(FakeProvider(replies=["[1]"]), [VACATIONS]).answer("¿?")

    assert answer.outcome is Outcome.LLM_NOT_FOUND


async def test_without_markers_all_relevant_fragments_are_cited() -> None:
    fake = FakeProvider(replies=["Tienes 15 días y puedes acumular dos periodos."])

    with structlog.testing.capture_logs() as logs:
        answer = await service(fake, [VACATIONS, ACCUMULATION, WEAK]).answer("¿Vacaciones?")

    assert [s.section for s in answer.sources] == ["2. Días de vacaciones", "4. Acumulación"]
    assert any(entry["event"] == "citations_fallback" for entry in logs)


async def test_invented_marker_is_ignored_and_logged() -> None:
    # CA-03.3: nunca aparece una fuente que no esté entre los fragmentos.
    fake = FakeProvider(replies=["Tienes 15 días [1][7]."])

    with structlog.testing.capture_logs() as logs:
        answer = await service(fake, [VACATIONS]).answer("¿Vacaciones?")

    assert answer.sources == [Source("politica_vacaciones.md", "2. Días de vacaciones")]
    assert answer.text == "Tienes 15 días."
    assert any(e["event"] == "citation_invalid" and e["markers"] == [7] for e in logs)


async def test_same_section_cited_twice_appears_once() -> None:
    duplicate = SearchHit(**{**VACATIONS.__dict__, "chunk_index": 2, "score": 0.88})
    fake = FakeProvider(replies=["Son 15 días [1] hábiles [2]."])

    answer = await service(fake, [VACATIONS, duplicate]).answer("¿Vacaciones?")

    assert answer.sources == [Source("politica_vacaciones.md", "2. Días de vacaciones")]


async def test_follow_up_searches_with_the_rewritten_question() -> None:
    history = [
        ChatMessage("user", "¿Cuántos días de vacaciones?"),
        ChatMessage("assistant", "15 días."),
    ]
    fake = FakeProvider(
        replies=["¿Se pueden acumular las vacaciones?", "Sí, hasta dos periodos [1]."]
    )

    answer = await service(fake, [ACCUMULATION]).answer("¿y se acumulan?", history)

    assert answer.search_question == "¿Se pueden acumular las vacaciones?"
    rewrite_call, answer_call = fake.calls
    assert rewrite_call.system == "REESCRIBE"
    assert list(answer_call.messages[:2]) == history
    assert "¿y se acumulan?" in answer_call.messages[-1].content  # el usuario, tal cual
    assert answer.usage.llm_calls == 2
    assert answer.usage.input_tokens > 0 and answer.usage.output_tokens > 0


async def test_provider_errors_propagate_to_the_bot() -> None:
    # RNF-05: el bot muestra "servicio no disponible".
    fake = FakeProvider(replies=[LLMUnavailableError("caído")])

    with pytest.raises(LLMUnavailableError):
        await service(fake, [VACATIONS]).answer("¿Vacaciones?")


async def test_outcome_is_logged_without_the_question_text() -> None:
    with structlog.testing.capture_logs() as logs:
        await service(FakeProvider(replies=["Sí [1]."]), [VACATIONS]).answer("pregunta privada")

    [entry] = [e for e in logs if e["event"] == "answer_outcome"]
    assert entry["outcome"] == "answered" and entry["cited"] == [1]
    assert "pregunta privada" not in str(logs)


# --- funciones puras ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "clean", "used"),
    [
        ("A [1]. B [2][3].", "A. B.", [1, 2, 3]),
        ("A [1, 2] y B [ 2 ].", "A y B.", [1, 2]),
        (
            "Paso 1: abre el portal [1].\nPaso 2: envía [2].",
            "Paso 1: abre el portal.\nPaso 2: envía.",
            [1, 2],
        ),
        ("Sin marcadores.", "Sin marcadores.", []),
        ("El código [ABC] no es marcador [1].", "El código [ABC] no es marcador.", [1]),
        # Visto en la evaluación: el punto de "p. m." más el del final de la oración.
        ("Atiende hasta las 7:00 p. m. [1].", "Atiende hasta las 7:00 p. m.", [1]),
        ("Espera... y luego sigue [2].", "Espera... y luego sigue.", [2]),
    ],
)
def test_extract_citations(text: str, clean: str, used: list[int]) -> None:
    citations = extract_citations(text, fragment_count=3)

    assert (citations.text, citations.used) == (clean, used)


def test_extract_citations_flags_out_of_range_numbers() -> None:
    assert extract_citations("A [0] [4] [2].", 3).invalid == [0, 4]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NO_ENCONTRADO", True),
        ("No_Encontrado.", True),
        ("«NO_ENCONTRADO»", True),
        ("No encontré nada", False),
        ("", False),
        ("Tienes 15 días. NO_ENCONTRADO", False),
    ],
)
def test_is_not_found(text: str, expected: bool) -> None:
    assert is_not_found(text) is expected


def test_source_labels() -> None:
    sources = [
        Source("a.md", "Intro"),
        Source("b.pdf", page=2),
        Source("c.docx", "Tabla", 4),
        Source("d.txt"),
    ]

    assert format_sources(sources) == (
        'Fuentes: a.md, sección "Intro"; b.pdf, página 2; c.docx, sección "Tabla", página 4; d.txt.'
    )
    assert format_sources([]) == ""
