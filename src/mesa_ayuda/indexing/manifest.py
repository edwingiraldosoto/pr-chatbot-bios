"""Manifest de indexación: qué se indexó, con qué ajustes y en qué versión (RF-09).

Es un JSON en ``data/manifest.json`` (formato en ``specs/data-model.md``). Con él, al
reiniciar se sabe qué documentos cambiaron sin recalcular embeddings de toda la carpeta
(CA-02.4, CA-09.1). La comparación es por huella SHA-256 del contenido: cambiar solo la
fecha de modificación no provoca reproceso.

Si el archivo falta o está dañado, :func:`load_manifest` devuelve ``None`` y el indexador
reconstruye todo: es más seguro que confiar en un estado a medias.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from mesa_ayuda.ingest.discovery import DiscoveredFile
from mesa_ayuda.logging_setup import get_logger

SCHEMA_VERSION = 1

DocumentStatus = Literal["indexed", "unreadable", "quarantined"]
_STATUSES = ("indexed", "unreadable", "quarantined")

log = get_logger(__name__)


@dataclass
class DocumentEntry:
    """Estado de un documento en el índice."""

    hash: str
    version: int
    status: DocumentStatus
    chunks: int = 0
    indexed_at: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    """Estado completo de la indexación."""

    settings: dict[str, Any]
    collection: str
    documents: dict[str, DocumentEntry] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def active_doc_keys(self) -> dict[str, str]:
        """``{ruta: doc_key}`` de los documentos que aportan fragmentos a la búsqueda."""
        return {
            source: f"{source}@{entry.version}"
            for source, entry in self.documents.items()
            if entry.status == "indexed"
        }


@dataclass(frozen=True)
class ManifestDiff:
    """Diferencias entre el manifest y la carpeta (rutas relativas, ordenadas)."""

    new: list[str]
    changed: list[str]
    unchanged: list[str]
    deleted: list[str]

    @property
    def has_changes(self) -> bool:
        """Si hay algo que procesar."""
        return bool(self.new or self.changed or self.deleted)


def diff(manifest: Manifest, files: Iterable[DiscoveredFile]) -> ManifestDiff:
    """Clasifica cada archivo como nuevo, cambiado, sin cambios o eliminado.

    Un archivo renombrado aparece como eliminado (ruta vieja) más nuevo (ruta nueva)
    (CA-09.4): la ruta es la identidad del documento y la que se cita al usuario. Un
    documento ilegible cuyo contenido no cambió queda "sin cambios": no se reintenta leerlo
    en cada reindexación.
    """
    current = {item.source: item.sha256 for item in files}
    new, changed, unchanged = [], [], []
    for source, sha256 in sorted(current.items()):
        entry = manifest.documents.get(source)
        if entry is None:
            new.append(source)
        elif entry.hash != sha256:
            changed.append(source)
        else:
            unchanged.append(source)
    deleted = sorted(set(manifest.documents) - set(current))
    return ManifestDiff(new=new, changed=changed, unchanged=unchanged, deleted=deleted)


def _parse(data: Any) -> Manifest:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("versión de esquema desconocida")
    documents: dict[str, DocumentEntry] = {}
    for source, raw in data["documents"].items():
        entry = DocumentEntry(**raw)
        if entry.status not in _STATUSES or not isinstance(entry.version, int):
            raise ValueError(f"entrada inválida para {source}")
        documents[source] = entry
    settings = data["settings"]
    collection = data["collection"]
    if not isinstance(settings, dict) or not isinstance(collection, str):
        raise ValueError("settings o collection inválidos")
    return Manifest(settings=settings, collection=collection, documents=documents)


def load_manifest(path: Path) -> Manifest | None:
    """Lee el manifest; ``None`` si no existe o no se puede interpretar."""
    if not path.exists():
        return None
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        log.warning("manifest_unreadable", path=str(path), error=str(error))
        return None


def save_manifest(path: Path, manifest: Manifest) -> None:
    """Escribe el manifest de forma atómica: nunca queda un JSON a medio escribir.

    Se escribe un temporal en la misma carpeta y se reemplaza con ``os.replace``, que es
    atómico en Windows y en Linux. Un corte de luz deja el manifest viejo o el nuevo.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": manifest.schema_version,
        "settings": manifest.settings,
        "collection": manifest.collection,
        "documents": {
            source: asdict(entry) for source, entry in sorted(manifest.documents.items())
        },
    }
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
