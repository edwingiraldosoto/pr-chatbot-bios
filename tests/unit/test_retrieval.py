"""Pruebas de la recuperación (T-18; RF-03, RF-04, CA-04.1)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.retrieval import Retriever
from mesa_ayuda.vectorstore import SearchHit


def hit(source: str, score: float) -> SearchHit:
    return SearchHit(
        text=f"texto de {source}",
        source=source,
        section="",
        page=0,
        chunk_index=0,
        doc_key=f"{source}@1",
        score=score,
    )


class RecordingSearch:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[int, int]] = []

    def __call__(self, embedding: Sequence[float], top_k: int) -> list[SearchHit]:
        self.calls.append((len(embedding), top_k))
        return self.hits[:top_k]


def test_threshold_separates_relevant_hits() -> None:
    search = RecordingSearch([hit("a.md", 0.91), hit("b.md", 0.80), hit("c.md", 0.79)])
    retriever = Retriever(FakeEmbedder(dimension=16), search, top_k=5, threshold=0.80)

    result = retriever.retrieve("¿Cuántos días de vacaciones tengo?")

    assert [h.source for h in result.relevant] == ["a.md", "b.md"]  # 0.80 cuenta (>=)
    assert len(result.hits) == 3 and result.found
    assert result.best_score == pytest.approx(0.91)
    assert search.calls == [(16, 5)]


def test_nothing_above_threshold_is_not_found() -> None:
    retriever = Retriever(
        FakeEmbedder(), RecordingSearch([hit("a.md", 0.74)]), top_k=5, threshold=0.80
    )

    result = retriever.retrieve("¿Cuál es la capital de Australia?")

    assert not result.found and result.relevant == []
    assert result.best_score == pytest.approx(0.74)  # se guarda en "sin respuesta" (RF-10)


def test_empty_index_has_no_best_score() -> None:
    result = Retriever(FakeEmbedder(), RecordingSearch([]), top_k=3, threshold=0.5).retrieve("x")

    assert result.best_score is None and not result.found


def test_hits_are_sorted_by_score_even_if_the_index_is_not() -> None:
    search = RecordingSearch([hit("b.md", 0.82), hit("a.md", 0.95)])

    result = Retriever(FakeEmbedder(), search, top_k=2, threshold=0.8).retrieve("x")

    assert [h.source for h in result.relevant] == ["a.md", "b.md"]


def test_top_k_is_passed_to_the_index() -> None:
    search = RecordingSearch([hit(f"{i}.md", 0.9) for i in range(10)])

    result = Retriever(FakeEmbedder(), search, top_k=3, threshold=0.8).retrieve("x")

    assert len(result.hits) == 3


@pytest.mark.parametrize(("top_k", "threshold"), [(0, 0.5), (3, 1.5), (3, -0.1)])
def test_invalid_parameters_are_rejected(top_k: int, threshold: float) -> None:
    with pytest.raises(ValueError):
        Retriever(FakeEmbedder(), RecordingSearch([]), top_k=top_k, threshold=threshold)
