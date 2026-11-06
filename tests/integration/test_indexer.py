"""Pruebas del indexador con Chroma real y embeddings falsos (T-15, T-16; RF-09)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.indexing.indexer import Indexer
from mesa_ayuda.vectorstore import ChunkRecord, collection_names

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "knowledge_base"

VACATIONS_V1 = "# Vacaciones\n\n## Días\n\nCada colaborador tiene 15 días hábiles de vacaciones.\n"
VACATIONS_V2 = "# Vacaciones\n\n## Días\n\nCada colaborador tiene 20 días hábiles de vacaciones.\n"
REMOTE = "Trabajo remoto: hasta 2 días por semana con auxilio de conectividad.\n"


class Workspace:
    """Carpeta de documentos + carpeta de datos + fábrica de indexadores."""

    def __init__(self, root: Path) -> None:
        self.kb = root / "knowledge_base"
        self.data = root / "data"
        self.kb.mkdir()
        self.embedder = FakeEmbedder()

    def write(self, name: str, content: str) -> Path:
        path = self.kb / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def indexer(self, **overrides: object) -> Indexer:
        options: dict[str, object] = {
            "knowledge_base": self.kb,
            "data_dir": self.data,
            "embedder": self.embedder,
            "chunk_size": 400,
            "chunk_overlap": 50,
            **overrides,
        }
        return Indexer(**options)  # type: ignore[arg-type]

    def search(self, indexer: Indexer, question: str, top_k: int = 5) -> list[str]:
        """Versiones de documento (sin repetir) que devuelve la búsqueda."""
        hits = indexer.search(self.embedder.embed_query(question), top_k)
        return sorted({hit.doc_key for hit in hits})

    def manifest(self) -> dict[str, object]:
        data: dict[str, object] = json.loads(
            (self.data / "manifest.json").read_text(encoding="utf-8")
        )
        return data


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


# --- Indexación inicial e incremental (T-15) ------------------------------------------------


def test_first_run_indexes_every_sample_document(ws: Workspace) -> None:
    for item in SAMPLE_DOCS.iterdir():
        shutil.copy(item, ws.kb / item.name)
    ws.write("~$borrador.docx", "bloqueo")

    summary = ws.indexer(chunk_size=800, chunk_overlap=150).reindex()

    assert summary.full_rebuild and summary.rebuild_reason == "no hay manifest o está dañado"
    assert sorted(summary.added) == sorted(item.name for item in SAMPLE_DOCS.iterdir())
    assert summary.ignored == 1 and not summary.unreadable


def test_restart_without_changes_does_not_reindex(ws: Workspace) -> None:
    # CA-02.4 y CA-09.1: reiniciar sin cambios no recalcula nada y la búsqueda funciona.
    ws.write("vacaciones.md", VACATIONS_V1)
    ws.indexer().reindex()

    restarted = ws.indexer()
    summary = restarted.reindex()

    assert not summary.has_changes and summary.unchanged == 1
    assert ws.search(restarted, "días de vacaciones") == ["vacaciones.md@1"]


def test_only_content_changes_trigger_reprocessing(ws: Workspace) -> None:
    path = ws.write("vacaciones.md", VACATIONS_V1)
    indexer = ws.indexer()
    indexer.reindex()

    path.touch()  # nueva fecha, mismo contenido
    assert not indexer.reindex().has_changes

    path.write_text(VACATIONS_V2, encoding="utf-8")
    summary = indexer.reindex()

    assert summary.updated == ["vacaciones.md"]
    assert ws.search(indexer, "días de vacaciones") == ["vacaciones.md@2"]


def test_updated_document_replaces_old_version_completely(ws: Workspace) -> None:
    # CA-09.2: la versión anterior deja de ser recuperable (y sus fragmentos se borran).
    path = ws.write("vacaciones.md", VACATIONS_V1)
    indexer = ws.indexer()
    indexer.reindex()

    path.write_text(VACATIONS_V2, encoding="utf-8")
    indexer.reindex()

    hits = indexer.search(ws.embedder.embed_query("15 días hábiles"), 10)
    assert {hit.doc_key for hit in hits} == {"vacaciones.md@2"}
    assert all("15 días" not in hit.text for hit in hits)


def test_deleted_document_is_no_longer_cited(ws: Workspace) -> None:
    # CA-09.3
    path = ws.write("vacaciones.md", VACATIONS_V1)
    ws.write("remoto.txt", REMOTE)
    indexer = ws.indexer()
    indexer.reindex()

    path.unlink()
    summary = indexer.reindex()

    assert summary.removed == ["vacaciones.md"]
    assert all(not key.startswith("vacaciones") for key in ws.search(indexer, "vacaciones"))
    assert [info.source for info in indexer.sources()] == ["remoto.txt"]


def test_renamed_document_is_removed_and_added(ws: Workspace) -> None:
    # CA-09.4
    path = ws.write("vacaciones.md", VACATIONS_V1)
    indexer = ws.indexer()
    indexer.reindex()

    path.rename(ws.kb / "politica_vacaciones.md")
    summary = indexer.reindex()

    assert (summary.added, summary.removed) == (["politica_vacaciones.md"], ["vacaciones.md"])
    assert ws.search(indexer, "vacaciones") == ["politica_vacaciones.md@1"]


def test_unreadable_new_version_withdraws_the_document(ws: Workspace) -> None:
    # CA-09.6: no se conserva la versión anterior si la nueva es ilegible.
    path = ws.kb / "reembolsos.pdf"
    shutil.copy(FIXTURES / "dos_paginas.pdf", path)
    indexer = ws.indexer()
    indexer.reindex()
    assert ws.search(indexer, "anticipo") == ["reembolsos.pdf@1"]

    shutil.copy(FIXTURES / "corrupto.pdf", path)
    summary = indexer.reindex()

    [(source, reason)] = summary.unreadable
    assert source == "reembolsos.pdf" and reason.startswith("PDF dañado")
    assert ws.search(indexer, "anticipo") == []
    [info] = indexer.sources()
    assert (info.status, info.warnings) == ("unreadable", (reason,))


def test_unreadable_file_does_not_stop_the_others(ws: Workspace) -> None:
    # CA-01.4
    shutil.copy(FIXTURES / "escaneado.pdf", ws.kb / "escaneado.pdf")
    ws.write("vacaciones.md", VACATIONS_V1)

    summary = ws.indexer().reindex()

    assert summary.added == ["vacaciones.md"]
    assert [source for source, _ in summary.unreadable] == ["escaneado.pdf"]
    assert summary.has_errors


def test_fixed_document_comes_back_with_a_new_version(ws: Workspace) -> None:
    path = ws.kb / "doc.pdf"
    shutil.copy(FIXTURES / "corrupto.pdf", path)
    indexer = ws.indexer()
    indexer.reindex()

    shutil.copy(FIXTURES / "dos_paginas.pdf", path)
    summary = indexer.reindex()

    assert summary.updated == ["doc.pdf"]
    assert ws.search(indexer, "anticipo") == ["doc.pdf@1"]


def test_empty_or_missing_folder_gives_empty_results(ws: Workspace) -> None:
    shutil.rmtree(ws.kb)
    indexer = ws.indexer()

    summary = indexer.reindex()

    assert summary.added == [] and indexer.search(ws.embedder.embed_query("x"), 5) == []


def test_search_before_first_reindex_is_empty(ws: Workspace) -> None:
    assert ws.indexer().search([1.0] * 256, 5) == []


def test_interrupted_update_leaves_no_orphans(ws: Workspace) -> None:
    # Simula un corte entre "agregar v2" y "borrar v1": la siguiente pasada limpia.
    path = ws.write("vacaciones.md", VACATIONS_V1)
    indexer = ws.indexer()
    indexer.reindex()
    store = indexer._store
    assert store is not None
    store.add(
        [ChunkRecord("vacaciones.md", 7, 0, "resto de un intento", "", 0, [1.0] + [0.0] * 255)]
    )

    path.write_text(VACATIONS_V2, encoding="utf-8")
    indexer.reindex()

    assert store.count_doc_key("vacaciones.md@7") == 0
    assert store.count() == store.count_doc_key("vacaciones.md@2")


# --- Reconstrucción completa (T-16) ---------------------------------------------------------


def test_changing_chunk_size_rebuilds_into_a_new_collection(ws: Workspace) -> None:
    # CA-09.5
    ws.write("vacaciones.md", VACATIONS_V1)
    ws.indexer().reindex()

    summary = ws.indexer(chunk_size=600).reindex()

    assert summary.full_rebuild
    assert summary.rebuild_reason == "cambió el modelo de embeddings o la fragmentación"
    assert collection_names(ws.data / "chroma") == ["kb_2"]
    assert ws.manifest()["collection"] == "kb_2"


def test_changing_embedding_model_rebuilds(ws: Workspace) -> None:
    ws.write("vacaciones.md", VACATIONS_V1)
    ws.indexer().reindex()

    other = FakeEmbedder(model_id="otro-modelo")
    summary = ws.indexer(embedder=other).reindex()

    assert summary.full_rebuild


def test_corrupt_manifest_rebuilds(ws: Workspace) -> None:
    ws.write("vacaciones.md", VACATIONS_V1)
    ws.indexer().reindex()
    (ws.data / "manifest.json").write_text("{dañado", encoding="utf-8")

    summary = ws.indexer().reindex()

    assert summary.full_rebuild and summary.added == ["vacaciones.md"]


def test_deleted_index_folder_rebuilds(ws: Workspace) -> None:
    ws.write("vacaciones.md", VACATIONS_V1)
    first = ws.indexer()
    first.reindex()
    # Equivale a borrar data/chroma con el bot detenido. (En Windows no se puede borrar la
    # carpeta mientras este proceso tiene abiertos sus archivos.)
    assert first._store is not None
    first._store._client.delete_collection("kb_1")

    restarted = ws.indexer()
    summary = restarted.reindex()

    assert summary.rebuild_reason == "el índice en disco no existe"
    assert ws.search(restarted, "vacaciones") == ["vacaciones.md@1"]
