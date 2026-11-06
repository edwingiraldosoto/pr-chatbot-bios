"""Pruebas del indexador con Chroma real y embeddings falsos (T-15, T-16, T-17; RF-08, RF-09)."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.indexing.indexer import Indexer, ReindexInProgressError
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


def test_full_reindex_on_request_keeps_serving_old_index_until_switch(ws: Workspace) -> None:
    # CA-08.2 y CA-08.4: durante "/reindex full" las búsquedas usan el índice anterior.
    ws.write("vacaciones.md", VACATIONS_V1)
    embedder = BlockingEmbedder()
    indexer = ws.indexer(embedder=embedder)
    indexer.reindex()

    embedder.block()
    worker = threading.Thread(target=indexer.reindex, kwargs={"full": True})
    worker.start()
    embedder.wait_until_blocked()

    during = indexer.search(embedder.embed_query("vacaciones"), 5)
    embedder.release()
    worker.join(timeout=10)

    assert [hit.doc_key for hit in during] == ["vacaciones.md@1"]
    assert collection_names(ws.data / "chroma") == ["kb_2"]
    assert ws.search(indexer, "vacaciones") == ["vacaciones.md@1"]


# --- Concurrencia (T-17) --------------------------------------------------------------------


class BlockingEmbedder(FakeEmbedder):
    """Embeddings falsos que pueden detenerse a mitad de una indexación."""

    def __init__(self) -> None:
        super().__init__()
        self._gate = threading.Event()
        self._gate.set()
        self._blocked = threading.Event()

    def block(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    def wait_until_blocked(self) -> None:
        assert self._blocked.wait(timeout=10), "la indexación no llegó a los embeddings"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._gate.is_set():
            self._blocked.set()
            self._gate.wait(timeout=10)
        return super().embed_documents(texts)


def test_second_reindex_is_rejected_while_one_runs(ws: Workspace) -> None:
    # CA-08.5
    ws.write("vacaciones.md", VACATIONS_V1)
    embedder = BlockingEmbedder()
    indexer = ws.indexer(embedder=embedder)
    embedder.block()
    worker = threading.Thread(target=indexer.reindex)
    worker.start()
    embedder.wait_until_blocked()

    assert indexer.is_reindexing
    with pytest.raises(ReindexInProgressError):
        indexer.reindex()

    embedder.release()
    worker.join(timeout=10)
    assert not indexer.is_reindexing


async def test_async_reindex_does_not_block_the_event_loop(ws: Workspace) -> None:
    # CA-08.4: mientras indexa en otro hilo, el bucle de eventos sigue atendiendo.
    ws.write("vacaciones.md", VACATIONS_V1)
    embedder = BlockingEmbedder()
    indexer = ws.indexer(embedder=embedder)
    embedder.block()

    task = asyncio.create_task(indexer.reindex_async())
    await asyncio.to_thread(embedder.wait_until_blocked)
    with pytest.raises(ReindexInProgressError):
        await indexer.reindex_async()
    ticks = 0
    for _ in range(3):
        await asyncio.sleep(0)
        ticks += 1
    embedder.release()
    summary = await task

    assert ticks == 3 and summary.added == ["vacaciones.md"]


def test_queries_during_updates_never_mix_versions_or_see_empty(ws: Workspace) -> None:
    # CA-09.2 bajo carga: un hilo actualiza el documento una y otra vez mientras otros
    # buscan sin parar. Cada búsqueda debe ver exactamente una versión, nunca cero.
    path = ws.write("vacaciones.md", VACATIONS_V1)
    indexer = ws.indexer()
    indexer.reindex()
    query = ws.embedder.embed_query("días hábiles de vacaciones")
    stop = threading.Event()
    problems: list[str] = []
    searches = 0

    def searcher() -> None:
        nonlocal searches
        while not stop.is_set():
            keys = {hit.doc_key for hit in indexer.search(query, 10)}
            searches += 1
            if len(keys) != 1:
                problems.append(f"versiones visibles: {sorted(keys)}")

    readers = [threading.Thread(target=searcher) for _ in range(3)]
    for reader in readers:
        reader.start()
    for version in range(2, 12):
        days = 15 + version
        path.write_text(VACATIONS_V1.replace("15", str(days)), encoding="utf-8")
        assert indexer.reindex().updated == ["vacaciones.md"]
    stop.set()
    for reader in readers:
        reader.join(timeout=10)

    assert problems == []
    assert searches > 10
    assert ws.search(indexer, "vacaciones") == ["vacaciones.md@11"]
