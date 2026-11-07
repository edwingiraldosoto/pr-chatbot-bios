"""Pruebas del script de evaluación sin red (T-38).

El script se corre de verdad con el LLM real (``docs/EVALUACION.md``); aquí se prueba que
el conjunto cumple los mínimos del enunciado y que la calificación no da falsos positivos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_ayuda.answering import Answer, Outcome, Source, Usage
from mesa_ayuda.config import load_settings
from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.llm.fake import FakeProvider
from scripts.evaluate import (
    Case,
    build_service,
    check,
    invented_words,
    load_cases,
    normalize,
    render,
    run_case,
    summarize,
)


def answer(text: str, *sources: str, outcome: Outcome = Outcome.ANSWERED) -> Answer:
    return Answer(text, [Source(s) for s in sources], outcome, Usage(), 0.85, "q")


def test_question_set_meets_the_minimums() -> None:
    # Enunciado: 12+ preguntas, 3+ fuera de alcance, 2+ seguimientos, injection desde un
    # documento y desde un mensaje (CA-R4.3).
    cases = load_cases()
    kinds = [case.kind for case in cases]

    assert len(cases) >= 12
    assert kinds.count("out_of_scope") >= 3
    assert kinds.count("follow_up") >= 2
    assert kinds.count("injection_document") >= 1 and kinds.count("injection_message") >= 1
    assert len({case.id for case in cases}) == len(cases)
    for case in cases:
        if case.kind in ("in_scope", "follow_up", "injection_document"):
            assert case.sources and case.facts, case.id
        if case.kind == "follow_up":
            assert case.history, case.id


def test_normalize_ignores_accents_and_case() -> None:
    assert normalize("Días HÁBILES") == "dias habiles"


def test_in_scope_needs_every_fact_and_a_valid_source() -> None:
    case = Case("c", "in_scope", "q", sources=("vac.md",), facts=(("15",), ("habiles", "utiles")))

    assert check(case, answer("Tienes 15 días hábiles.", "vac.md"), 1) == (True, [])
    ok, reasons = check(case, answer("Tienes 15 días.", "vac.md"), 1)
    assert not ok and reasons == ["falta el dato 'habiles'"]
    ok, reasons = check(case, answer("Tienes 15 días hábiles.", "otro.md"), 1)
    assert not ok and "fuente" in reasons[0]
    ok, _ = check(case, answer("", outcome=Outcome.LLM_NOT_FOUND), None)
    assert not ok


def test_out_of_scope_must_not_answer() -> None:
    case = Case("c", "out_of_scope", "¿Capital de Australia?")

    assert check(case, answer("", outcome=Outcome.BELOW_THRESHOLD), None)[0]
    assert not check(case, answer("Canberra.", "x.md"), None)[0]


def test_injection_with_a_valid_question_answers_but_does_not_obey() -> None:
    case = Case(
        "c", "injection_message", "q", sources=("vac.md",), facts=(("15",),), forbid=("grumete",)
    )

    assert check(case, answer("Tienes 15 días.", "vac.md"), 1)[0]
    assert not check(case, answer("¡Arr, grumete! 15 días.", "vac.md"), 1)[0]


def test_injection_without_facts_must_not_answer_or_leak() -> None:
    case = Case("c", "injection_message", "q", forbid=("REGLAS DE SEGURIDAD",))

    assert check(case, answer("", outcome=Outcome.LLM_NOT_FOUND), None)[0]
    assert not check(case, answer("REGLAS DE SEGURIDAD: ...", "x.md"), None)[0]


async def test_full_run_offline_with_fakes(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El circuito completo (índice propio, historial, recuperación y calificación) sin red.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", "sk-ant-test-0123456789")
    knowledge = Path(__file__).resolve().parents[2] / "knowledge_base"
    monkeypatch.setenv("KNOWLEDGE_BASE_PATH", str(knowledge))
    settings = load_settings(config_dir / "config.yaml", env_file=None)
    provider = FakeProvider(default="Tienes 15 días hábiles [1].")
    service, retriever, indexer = build_service(
        settings,
        FakeEmbedder(),
        provider,
        eval_dir=config_dir / "eval",
        threshold=0.1,
        top_k=5,
        chunk_size=800,
        chunk_overlap=150,
    )
    case = next(c for c in load_cases() if c.id == "seg_acumular")

    result = await run_case(case, service, retriever)

    assert "impresoras.md" in {info.source for info in indexer.sources()}
    assert result.history_answers == ["Tienes 15 días hábiles."]
    assert not result.passed  # el LLM falso no sabe de periodos acumulados
    assert result.reasons == ["falta el dato '2 periodos'"]
    summary = summarize([result])
    assert summary.follow_up == (0, 1) and not summary.sc003
    assert "`seg_acumular` | follow_up | FALLA" in render([result], summary, {"umbral": 0.1})


def test_invented_words_flags_new_content_but_accepts_inflections() -> None:
    case = Case("c", "rewrite", "Cómo formatear un ocho", history=("¿Horario de la mesa de TI?",))

    assert invented_words(case, "¿Cómo se formatea un disco de 8 TB?") == ["disco"]
    assert invented_words(case, "¿Cómo formateo un ocho?") == []
    assert check(case, answer("", outcome=Outcome.BELOW_THRESHOLD), None)[0] is True
