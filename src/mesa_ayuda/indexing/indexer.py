"""Indexador incremental con versiones por documento (RF-02, RF-08, RF-09).

Garantía principal (CA-09.2): mientras se actualiza un documento, ninguna búsqueda ve su
versión vieja mezclada con la nueva, ni el documento vacío. Se logra así:

1. La versión nueva se agrega con otra clave (``ruta@N+1``); como las búsquedas filtran por
   las claves activas, sus fragmentos todavía son invisibles.
2. El cambio de versión activa es una asignación bajo un candado de escritura, que espera a
   que terminen las búsquedas en curso. Desde ese instante todas ven solo la versión nueva.
3. Recién entonces se borran los fragmentos de la versión vieja: ya nadie puede verlos.

La reconstrucción completa (cambio de modelo o de fragmentación, manifest dañado, índice
incompleto, o ``/reindex full``) usa el mismo principio a nivel de colección: se indexa
todo en una colección nueva, se cambia el puntero y se borra la vieja. Mientras tanto el
bot sigue respondiendo con el índice anterior (CA-08.4).

Solo corre una reindexación a la vez; una segunda solicitud se rechaza de inmediato con
:class:`ReindexInProgressError` en lugar de esperar (CA-08.5).
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mesa_ayuda.embeddings import Embedder, EmbeddingModelError
from mesa_ayuda.indexing.manifest import (
    DocumentEntry,
    Manifest,
    diff,
    load_manifest,
    save_manifest,
)
from mesa_ayuda.ingest.chunker import chunk_sections
from mesa_ayuda.ingest.discovery import DiscoveredFile, discover
from mesa_ayuda.ingest.loaders import UnreadableDocumentError, load_document
from mesa_ayuda.logging_setup import get_logger
from mesa_ayuda.vectorstore import (
    COLLECTION_PREFIX,
    ChunkRecord,
    SearchHit,
    VectorStore,
    collection_names,
    doc_key,
)

log = get_logger(__name__)

_COLLECTION_NUMBER = re.compile(rf"^{COLLECTION_PREFIX}(\d+)$")


class ReindexInProgressError(Exception):
    """Ya hay una reindexación en curso (CA-08.5)."""


@dataclass
class ReindexSummary:
    """Resultado de una reindexación, para el log y el mensaje al administrador."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    unreadable: list[tuple[str, str]] = field(default_factory=list)  # (ruta, motivo)
    ignored: int = 0  # archivos ocultos, temporales o de formato no soportado
    full_rebuild: bool = False
    rebuild_reason: str = ""
    duration_s: float = 0.0

    @property
    def has_changes(self) -> bool:
        """Si cambió algo en el índice."""
        return bool(self.added or self.updated or self.removed or self.full_rebuild)

    @property
    def has_errors(self) -> bool:
        """Si algún documento no se pudo leer."""
        return bool(self.unreadable)


@dataclass(frozen=True)
class SourceInfo:
    """Documento del índice, para ``/fuentes``."""

    source: str
    status: str
    chunks: int
    indexed_at: str
    warnings: tuple[str, ...]


class _ReadWriteLock:
    """Muchos lectores a la vez o un escritor; los escritores tienen prioridad."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        with self._condition:
            while self._writer or self._writers_waiting:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        with self._condition:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._condition.wait()
            self._writers_waiting -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Indexer:
    """Mantiene el índice vectorial sincronizado con la carpeta de documentos."""

    def __init__(
        self,
        *,
        knowledge_base: Path,
        data_dir: Path,
        embedder: Embedder,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self._knowledge_base = knowledge_base
        self._chroma_path = data_dir / "chroma"
        self._manifest_path = data_dir / "manifest.json"
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

        self._reindex_lock = threading.Lock()  # una reindexación a la vez
        self._rw = _ReadWriteLock()  # búsquedas frente a cambios de versión
        self._store: VectorStore | None = None
        self._manifest: Manifest | None = None
        self._active: dict[str, str] = {}  # ruta → doc_key activo

    # --- Consultas (cualquier hilo) ---------------------------------------------------------

    def search(self, embedding: Sequence[float], top_k: int) -> list[SearchHit]:
        """Fragmentos más parecidos, solo de las versiones activas."""
        with self._rw.read():
            if self._store is None:
                return []
            return self._store.query(embedding, top_k, list(self._active.values()))

    def sources(self) -> list[SourceInfo]:
        """Documentos conocidos con su estado (indexados e ilegibles)."""
        with self._rw.read():
            if self._manifest is None:
                return []
            return [
                SourceInfo(
                    source=source,
                    status=entry.status,
                    chunks=entry.chunks,
                    indexed_at=entry.indexed_at,
                    warnings=tuple(entry.warnings),
                )
                for source, entry in sorted(self._manifest.documents.items())
            ]

    @property
    def is_reindexing(self) -> bool:
        """Si hay una reindexación en curso."""
        return self._reindex_lock.locked()

    # --- Reindexación -----------------------------------------------------------------------

    async def reindex_async(self, *, full: bool = False) -> ReindexSummary:
        """Igual que :meth:`reindex`, en un hilo aparte para no bloquear el bot."""
        if self.is_reindexing:  # rechazo inmediato, sin crear el hilo
            raise ReindexInProgressError
        return await asyncio.to_thread(self.reindex, full=full)

    def reindex(self, *, full: bool = False) -> ReindexSummary:
        """Sincroniza el índice con la carpeta.

        Args:
            full: reconstruye todo aunque no haya cambios (``/reindex full``).

        Raises:
            ReindexInProgressError: si ya hay otra reindexación en curso.
            EmbeddingModelError: si el modelo de embeddings no se puede cargar.
        """
        if not self._reindex_lock.acquire(blocking=False):
            raise ReindexInProgressError
        started = time.monotonic()
        try:
            discovery = discover(self._knowledge_base)
            reason = self._rebuild_reason(full)
            log.info("reindex_started", full=bool(reason), reason=reason or None)
            if reason:
                summary = self._rebuild(discovery.files, reason)
            else:
                summary = self._incremental(discovery.files)
            summary.ignored = len(discovery.skipped)
            summary.duration_s = round(time.monotonic() - started, 2)
            log.info(
                "reindex_finished",
                added=len(summary.added),
                updated=len(summary.updated),
                removed=len(summary.removed),
                unchanged=summary.unchanged,
                unreadable=len(summary.unreadable),
                ignored=summary.ignored,
                full_rebuild=summary.full_rebuild,
                duration_s=summary.duration_s,
            )
            return summary
        finally:
            self._reindex_lock.release()

    def _settings_snapshot(self) -> dict[str, Any]:
        return {
            "embedding_model": self._embedder.model_id,
            "chunk_size": self._chunk_size,
            "chunk_overlap": self._chunk_overlap,
        }

    def _rebuild_reason(self, full: bool) -> str:
        """Motivo para reconstruir todo, o ``""`` si basta con el modo incremental."""
        if full:
            return "solicitada"
        if self._manifest is None:  # primera reindexación de este proceso
            manifest = load_manifest(self._manifest_path)
            if manifest is None:
                return "no hay manifest o está dañado"
            if manifest.settings != self._settings_snapshot():
                return "cambió el modelo de embeddings o la fragmentación"
            if manifest.collection not in collection_names(self._chroma_path):
                return "el índice en disco no existe"
            store = VectorStore(self._chroma_path, manifest.collection)
            expected = sum(
                entry.chunks for entry in manifest.documents.values() if entry.status == "indexed"
            )
            if store.count() < expected:
                return "el índice en disco está incompleto"
            with self._rw.write():
                self._manifest = manifest
                self._store = store
                self._active = manifest.active_doc_keys()
        elif self._manifest.settings != self._settings_snapshot():
            return "cambió el modelo de embeddings o la fragmentación"
        return ""

    def _incremental(self, files: list[DiscoveredFile]) -> ReindexSummary:
        assert self._manifest is not None and self._store is not None
        summary = ReindexSummary()
        changes = diff(self._manifest, files)
        summary.unchanged = len(changes.unchanged)
        by_source = {item.source: item for item in files}

        for source in changes.new + changes.changed:
            outcome = self._index_file(
                by_source[source], self._manifest, self._store, self._active, live=True
            )
            if outcome is None:
                (summary.added if source in changes.new else summary.updated).append(source)
            else:
                summary.unreadable.append((source, outcome))

        for source in changes.deleted:
            self._remove(source)
            summary.removed.append(source)

        orphans = self._store.delete_except(set(self._active.values()))
        if orphans:
            log.warning("orphan_chunks_removed", count=orphans)
        return summary

    def _rebuild(self, files: list[DiscoveredFile], reason: str) -> ReindexSummary:
        summary = ReindexSummary(full_rebuild=True, rebuild_reason=reason)
        numbers = [
            int(match.group(1))
            for name in collection_names(self._chroma_path)
            if (match := _COLLECTION_NUMBER.match(name))
        ]
        name = f"{COLLECTION_PREFIX}{max(numbers, default=0) + 1}"
        store = VectorStore(self._chroma_path, name)
        manifest = Manifest(settings=self._settings_snapshot(), collection=name)
        active: dict[str, str] = {}

        for item in files:
            outcome = self._index_file(item, manifest, store, active, live=False)
            if outcome is None:
                summary.added.append(item.source)
            else:
                summary.unreadable.append((item.source, outcome))

        # Cambio de puntero: desde aquí las búsquedas usan la colección nueva.
        with self._rw.write():
            self._store, self._manifest, self._active = store, manifest, active
            save_manifest(self._manifest_path, manifest)
        store.drop_other_collections()
        log.info("index_rebuilt", collection=name, reason=reason, documents=len(active))
        return summary

    def _index_file(
        self,
        item: DiscoveredFile,
        manifest: Manifest,
        store: VectorStore,
        active: dict[str, str],
        *,
        live: bool,
    ) -> str | None:
        """Indexa un archivo. Devuelve ``None`` si quedó indexado o el motivo si no.

        Con ``live`` el índice está en uso: el cambio de versión y el retiro de la versión
        anterior se hacen bajo el candado de escritura.
        """
        previous = manifest.documents.get(item.source)
        last_version = previous.version if previous else 0
        try:
            sections = load_document(item.path)
            chunks = chunk_sections(sections, self._chunk_size, self._chunk_overlap)
            vectors = self._embedder.embed_documents([chunk.text for chunk in chunks])
        except UnreadableDocumentError as error:
            reason = str(error)
        except EmbeddingModelError:
            raise  # falla del sistema, no del documento
        except Exception as error:
            log.exception("document_failed", source=item.source)
            reason = f"error inesperado ({type(error).__name__})"
        else:
            version = last_version + 1
            store.delete_doc_key(doc_key(item.source, version))  # restos de un intento cortado
            store.add(
                [
                    ChunkRecord(
                        source=item.source,
                        version=version,
                        chunk_index=chunk.index,
                        text=chunk.text,
                        section=chunk.section,
                        page=chunk.page,
                        embedding=vector,
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
            entry = DocumentEntry(
                hash=item.sha256,
                version=version,
                status="indexed",
                chunks=len(chunks),
                indexed_at=_now(),
            )
            self._activate(item.source, entry, manifest, store, active, live=live)
            log.info("document_indexed", source=item.source, version=version, chunks=len(chunks))
            return None

        # Ilegible: si había una versión indexada, se retira (CA-09.6).
        entry = DocumentEntry(
            hash=item.sha256, version=last_version, status="unreadable", warnings=[reason]
        )
        self._activate(item.source, entry, manifest, store, active, live=live)
        log.warning("document_unreadable", source=item.source, reason=reason)
        return reason

    def _activate(
        self,
        source: str,
        entry: DocumentEntry,
        manifest: Manifest,
        store: VectorStore,
        active: dict[str, str],
        *,
        live: bool,
    ) -> None:
        """Registra el nuevo estado del documento y retira su versión anterior."""
        old_key = active.get(source)
        new_key = doc_key(source, entry.version) if entry.status == "indexed" else None
        if live:
            with self._rw.write():
                self._set_active(active, source, new_key)
                manifest.documents[source] = entry
                save_manifest(self._manifest_path, manifest)
        else:
            self._set_active(active, source, new_key)
            manifest.documents[source] = entry
        # Fuera del candado: ninguna búsqueda puede estar usando ya la clave vieja.
        if old_key and old_key != new_key:
            store.delete_doc_key(old_key)

    @staticmethod
    def _set_active(active: dict[str, str], source: str, key: str | None) -> None:
        if key is None:
            active.pop(source, None)
        else:
            active[source] = key

    def _remove(self, source: str) -> None:
        assert self._manifest is not None and self._store is not None
        with self._rw.write():
            old_key = self._active.pop(source, None)
            self._manifest.documents.pop(source, None)
            save_manifest(self._manifest_path, self._manifest)
        if old_key:
            self._store.delete_doc_key(old_key)
        log.info("document_removed", source=source)
