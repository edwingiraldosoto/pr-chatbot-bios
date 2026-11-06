"""Pruebas de la envoltura de Chroma con un Chroma real en disco (T-13; RF-02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_ayuda.vectorstore import ChunkRecord, VectorStore, collection_names


def record(source: str, version: int, index: int, vector: list[float]) -> ChunkRecord:
    return ChunkRecord(
        source=source,
        version=version,
        chunk_index=index,
        text=f"{source} v{version} #{index}",
        section="2. Días" if index == 0 else "",
        page=3 if source.endswith(".pdf") else 0,
        embedding=vector,
    )


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "chroma", "kb_1")


def test_cosine_similarity_ignores_vector_length(store: VectorStore) -> None:
    # Si la colección usara L2 (el valor por defecto de Chroma), un vector más largo en la
    # misma dirección no daría similitud 1.
    store.add([record("a.md", 1, 0, [1.0, 0.0, 0.0]), record("b.md", 1, 0, [0.0, 1.0, 0.0])])

    hits = store.query([5.0, 0.0, 0.0], top_k=2, active_doc_keys={"a.md@1", "b.md@1"})

    assert [hit.source for hit in hits] == ["a.md", "b.md"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0, abs=1e-6)


def test_metadata_is_returned_for_citations(store: VectorStore) -> None:
    store.add([record("reembolso.pdf", 2, 0, [1.0, 0.0])])

    [hit] = store.query([1.0, 0.0], top_k=1, active_doc_keys={"reembolso.pdf@2"})

    assert (hit.source, hit.section, hit.page, hit.chunk_index, hit.doc_key) == (
        "reembolso.pdf",
        "2. Días",
        3,
        0,
        "reembolso.pdf@2",
    )


def test_query_only_sees_active_versions(store: VectorStore) -> None:
    store.add([record("a.md", 1, 0, [1.0, 0.0]), record("a.md", 2, 0, [1.0, 0.1])])

    hits = store.query([1.0, 0.0], top_k=5, active_doc_keys={"a.md@2"})

    assert [hit.doc_key for hit in hits] == ["a.md@2"]
    assert store.query([1.0, 0.0], top_k=5, active_doc_keys=set()) == []


def test_delete_by_version_and_orphans(store: VectorStore) -> None:
    store.add(
        [
            record("a.md", 1, 0, [1.0, 0.0]),
            record("a.md", 1, 1, [0.9, 0.1]),
            record("b.md", 1, 0, [0.0, 1.0]),
            record("c.md", 3, 0, [0.5, 0.5]),
        ]
    )

    store.delete_doc_key("a.md@1")
    removed = store.delete_except({"b.md@1"})

    assert (removed, store.count()) == (1, 1)
    assert store.count_doc_key("b.md@1") == 1


def test_index_persists_across_instances(tmp_path: Path) -> None:
    # CA-02.4: lo indexado sigue ahí al "reiniciar" (nueva instancia sobre la misma carpeta).
    VectorStore(tmp_path / "chroma", "kb_1").add([record("a.md", 1, 0, [1.0, 0.0])])

    reopened = VectorStore(tmp_path / "chroma", "kb_1")

    assert reopened.count() == 1


def test_large_batches_are_split(store: VectorStore) -> None:
    records = [record("grande.md", 1, i, [1.0, i / 6000]) for i in range(6000)]

    store.add(records)

    assert store.count_doc_key("grande.md@1") == 6000


def test_other_collections_can_be_dropped(tmp_path: Path) -> None:
    VectorStore(tmp_path / "chroma", "kb_1")
    current = VectorStore(tmp_path / "chroma", "kb_2")

    assert current.drop_other_collections() == ["kb_1"]
    assert collection_names(tmp_path / "chroma") == ["kb_2"]
