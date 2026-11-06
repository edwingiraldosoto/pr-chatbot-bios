"""Pruebas de los embeddings (T-12; RF-02).

El modelo real (465 MB) no se descarga en la suite: ``LocalEmbedder`` se prueba con un
``TextEmbedding`` falso que registra lo que recibe. La prueba con el modelo real está en
``tests/integration/test_embeddings_model.py`` y solo corre con ``RUN_MODEL_TESTS=1``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import fastembed
import pytest

from mesa_ayuda.embeddings import EmbeddingModelError, FakeEmbedder, LocalEmbedder


class StubTextEmbedding:
    """Imita ``fastembed.TextEmbedding`` sin red ni modelo."""

    received: ClassVar[list[str]] = []
    custom: ClassVar[list[str]] = []
    instances: ClassVar[int] = 0

    def __init__(self, model_name: str, cache_dir: str | None = None, **_: Any) -> None:
        type(self).instances += 1
        self.model_name = model_name

    @staticmethod
    def list_supported_models() -> list[dict[str, Any]]:
        return [{"model": "BAAI/bge-small-en-v1.5"}]

    @classmethod
    def add_custom_model(cls, model: str, **_: Any) -> None:
        cls.custom.append(model)

    def embed(self, documents: Iterable[str], batch_size: int = 256) -> Iterator[list[float]]:
        for text in documents:
            type(self).received.append(text)
            yield [1.0, 0.0]


@pytest.fixture
def stub_fastembed(monkeypatch: pytest.MonkeyPatch) -> type[StubTextEmbedding]:
    StubTextEmbedding.received = []
    StubTextEmbedding.custom = []
    StubTextEmbedding.instances = 0
    monkeypatch.setattr(fastembed, "TextEmbedding", StubTextEmbedding)
    return StubTextEmbedding


def test_e5_prefixes_are_applied(stub_fastembed: type[StubTextEmbedding], tmp_path: Path) -> None:
    embedder = LocalEmbedder("intfloat/multilingual-e5-small", tmp_path)

    embedder.embed_documents(["15 días hábiles"])
    embedder.embed_query("¿cuántos días?")

    assert stub_fastembed.received == ["passage: 15 días hábiles", "query: ¿cuántos días?"]
    assert stub_fastembed.custom == ["intfloat/multilingual-e5-small"]


def test_catalog_model_has_no_prefixes(
    stub_fastembed: type[StubTextEmbedding], tmp_path: Path
) -> None:
    embedder = LocalEmbedder("BAAI/bge-small-en-v1.5", tmp_path)

    embedder.embed_query("hola")

    assert stub_fastembed.received == ["hola"]
    assert stub_fastembed.custom == []


def test_model_loads_once_and_lazily(
    stub_fastembed: type[StubTextEmbedding], tmp_path: Path
) -> None:
    embedder = LocalEmbedder("intfloat/multilingual-e5-small", tmp_path)
    assert stub_fastembed.instances == 0

    embedder.embed_query("a")
    embedder.embed_documents(["b", "c"])

    assert stub_fastembed.instances == 1
    assert embedder.embed_documents([]) == []


def test_unknown_model_names_the_parameter(
    stub_fastembed: type[StubTextEmbedding], tmp_path: Path
) -> None:
    embedder = LocalEmbedder("modelo/que-no-existe", tmp_path)

    with pytest.raises(EmbeddingModelError, match=r"EMBEDDING_MODEL.*modelo/que-no-existe"):
        embedder.load()


def test_download_failure_explains_internet_requirement(
    monkeypatch: pytest.MonkeyPatch, stub_fastembed: type[StubTextEmbedding], tmp_path: Path
) -> None:
    def offline(*_: Any, **__: Any) -> None:
        raise ConnectionError("sin red")

    monkeypatch.setattr(StubTextEmbedding, "__init__", offline)

    with pytest.raises(EmbeddingModelError, match="necesita internet"):
        LocalEmbedder("intfloat/multilingual-e5-small", tmp_path).load()


def test_real_catalog_lists_e5_family_unknown_model() -> None:
    # Sin el stub: el catálogo real de fastembed se consulta sin red.
    with pytest.raises(EmbeddingModelError, match="intfloat/multilingual-e5-small"):
        LocalEmbedder("modelo/inexistente", Path(".")).load()


# --- FakeEmbedder ---------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_vectors_are_normalized_and_deterministic() -> None:
    embedder = FakeEmbedder()

    first = embedder.embed_query("Vacaciones de quince días")
    second = FakeEmbedder().embed_documents(["Vacaciones de quince días"])[0]

    assert first == second
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)


def test_fake_similarity_follows_shared_words() -> None:
    embedder = FakeEmbedder()
    query = embedder.embed_query("¿Cuántos días de vacaciones tengo?")
    related, unrelated = embedder.embed_documents(
        ["Tienes 15 días hábiles de vacaciones.", "El auxilio de conectividad es mensual."]
    )

    assert cosine(query, related) > cosine(query, unrelated)


def test_fake_ignores_case_and_accents() -> None:
    embedder = FakeEmbedder()

    assert embedder.embed_query("Política") == embedder.embed_query("politica")


def test_fake_empty_text_still_gives_a_valid_vector() -> None:
    vector = FakeEmbedder(dimension=8).embed_query("¿?")

    assert len(vector) == 8 and math.isclose(sum(v * v for v in vector), 1.0)
