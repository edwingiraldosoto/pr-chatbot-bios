"""Envoltura de ChromaDB persistente (RF-02, CA-02.4, CA-02.5).

Encapsula los tres detalles de Chroma que, si se olvidan, rompen la búsqueda sin dar error:

- La colección se crea con distancia coseno; por defecto Chroma usa L2 y los puntajes no
  serían comparables con el umbral.
- La similitud se calcula como ``1 − distancia``.
- Las búsquedas filtran por las versiones activas de cada documento (``doc_key``), para que
  nunca aparezca una versión vieja ni una a medio indexar (CA-09.2).

Los embeddings se calculan fuera (``embeddings.py``): la colección se crea sin función de
embeddings para que Chroma no descargue su modelo por defecto (en inglés).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings as ChromaSettings

COLLECTION_PREFIX = "kb_"


@dataclass(frozen=True)
class ChunkRecord:
    """Fragmento con su vector y metadatos, listo para guardar."""

    source: str
    version: int
    chunk_index: int
    text: str
    section: str
    page: int
    embedding: Sequence[float]

    @property
    def doc_key(self) -> str:
        """Clave de la versión del documento: ``<ruta>@<versión>``."""
        return doc_key(self.source, self.version)

    @property
    def id(self) -> str:
        """Identificador único del fragmento en la colección."""
        return f"{self.doc_key}#{self.chunk_index}"


@dataclass(frozen=True)
class SearchHit:
    """Fragmento recuperado con su similitud coseno (1 = idéntico)."""

    text: str
    source: str
    section: str
    page: int
    chunk_index: int
    doc_key: str
    score: float


def doc_key(source: str, version: int) -> str:
    """Clave de una versión de documento."""
    return f"{source}@{version}"


def _vectors(vectors: Iterable[Sequence[float]]) -> list[Sequence[float] | Sequence[int]]:
    return [list(vector) for vector in vectors]


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _client(path: Path) -> ClientAPI:
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(path),
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
    )


def collection_names(path: Path) -> list[str]:
    """Colecciones del índice que existen en ``path``, sin crear ninguna."""
    return sorted(
        name
        for name in (c.name for c in _client(path).list_collections())
        if name.startswith(COLLECTION_PREFIX)
    )


class VectorStore:
    """Una colección de Chroma persistente en disco, sin servicios externos (CA-02.5)."""

    def __init__(self, path: Path, collection: str) -> None:
        self._path = path
        self._client = _client(path)
        self.name = collection
        self._collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

    # --- Colecciones ------------------------------------------------------------------------

    def drop_other_collections(self) -> list[str]:
        """Borra las demás colecciones del índice (p. ej. tras una reconstrucción)."""
        others = [name for name in collection_names(self._path) if name != self.name]
        for name in others:
            self._client.delete_collection(name)
        return others

    # --- Escritura --------------------------------------------------------------------------

    def add(self, records: Sequence[ChunkRecord]) -> None:
        """Agrega fragmentos; reemplaza los que tengan el mismo id."""
        batch = max(1, self._client.get_max_batch_size())
        for start in range(0, len(records), batch):
            part = records[start : start + batch]
            self._collection.upsert(
                ids=[record.id for record in part],
                documents=[record.text for record in part],
                embeddings=_vectors(record.embedding for record in part),
                metadatas=[
                    {
                        "source": record.source,
                        "doc_key": record.doc_key,
                        "version": record.version,
                        "section": record.section,
                        "page": record.page,
                        "chunk_index": record.chunk_index,
                    }
                    for record in part
                ],
            )

    def delete_doc_key(self, key: str) -> None:
        """Borra todos los fragmentos de una versión de documento."""
        self._collection.delete(where={"doc_key": key})

    def delete_except(self, keep: Collection[str]) -> int:
        """Borra los fragmentos de versiones que no están en ``keep`` (huérfanos).

        Quedan huérfanos si el proceso se interrumpe entre agregar una versión nueva y
        borrar la anterior. Devuelve cuántos fragmentos se borraron.
        """
        stored = self._collection.get(include=["metadatas"])
        orphan_ids = [
            item_id
            for item_id, metadata in zip(stored["ids"], stored["metadatas"] or [], strict=True)
            if metadata.get("doc_key") not in keep
        ]
        if orphan_ids:
            self._collection.delete(ids=orphan_ids)
        return len(orphan_ids)

    # --- Lectura ----------------------------------------------------------------------------

    def count(self) -> int:
        """Número de fragmentos guardados."""
        return int(self._collection.count())

    def count_doc_key(self, key: str) -> int:
        """Número de fragmentos de una versión de documento."""
        return len(self._collection.get(where={"doc_key": key}, include=[])["ids"])

    def query(
        self, embedding: Sequence[float], top_k: int, active_doc_keys: Collection[str]
    ) -> list[SearchHit]:
        """Los ``top_k`` fragmentos más parecidos entre las versiones activas."""
        if not active_doc_keys or top_k <= 0:
            return []
        where: dict[str, Any] = {"doc_key": {"$in": sorted(active_doc_keys)}}
        result = self._collection.query(
            query_embeddings=_vectors([embedding]),
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        documents = (result["documents"] or [[]])[0]
        metadatas = (result["metadatas"] or [[]])[0]
        distances = (result["distances"] or [[]])[0]
        for text, metadata, distance in zip(documents, metadatas, distances, strict=True):
            hits.append(
                SearchHit(
                    text=text,
                    source=str(metadata["source"]),
                    section=str(metadata.get("section", "")),
                    page=_as_int(metadata.get("page")),
                    chunk_index=_as_int(metadata.get("chunk_index")),
                    doc_key=str(metadata["doc_key"]),
                    score=1.0 - float(distance),
                )
            )
        return hits
