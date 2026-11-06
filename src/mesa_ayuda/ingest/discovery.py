"""Descubrimiento de documentos en la carpeta de conocimiento (RF-01, CA-01.3).

Recorre la carpeta (con subcarpetas), descarta lo que no debe indexarse y calcula la huella
SHA-256 de cada archivo. La huella es la que decide después si un documento cambió: la fecha
de modificación no sirve porque copiar o descomprimir archivos la altera sin cambiar el
contenido.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mesa_ayuda.logging_setup import get_logger

SUPPORTED_EXTENSIONS = frozenset({".md", ".txt", ".pdf", ".docx"})

SkipReason = Literal["hidden", "office_temp", "unsupported", "read_error"]

_HASH_BLOCK = 1024 * 1024

log = get_logger(__name__)


@dataclass(frozen=True)
class DiscoveredFile:
    """Archivo candidato a indexarse."""

    path: Path
    source: str  # ruta relativa a la carpeta, con "/" en cualquier sistema operativo
    sha256: str
    size: int


@dataclass(frozen=True)
class SkippedFile:
    """Archivo o carpeta ignorado, con el motivo."""

    source: str
    reason: SkipReason


@dataclass
class DiscoveryResult:
    """Resultado de recorrer la carpeta de conocimiento."""

    files: list[DiscoveredFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    root_exists: bool = True


def file_sha256(path: Path) -> str:
    """Huella SHA-256 del contenido, con el prefijo ``sha256:``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _is_hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    # En Windows, "oculto" es un atributo y no depende del nombre (p. ej. Thumbs.db).
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))


def _skip_reason(entry: os.DirEntry[str]) -> SkipReason | None:
    if entry.name.startswith("~$"):  # bloqueo temporal de Word mientras el archivo está abierto
        return "office_temp"
    if _is_hidden(entry):
        return "hidden"
    if Path(entry.name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported"
    return None


def discover(root: Path) -> DiscoveryResult:
    """Lista los documentos soportados bajo ``root``, ordenados por ruta relativa.

    No sigue enlaces simbólicos a carpetas (evita ciclos). Una carpeta inexistente no es un
    error: el bot arranca y responde "no encontrado" (caso límite del spec).
    """
    result = DiscoveryResult()
    if not root.is_dir():
        result.root_exists = False
        log.warning("knowledge_base_missing", path=str(root))
        return result

    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda item: item.name)
        except OSError as error:
            source = folder.relative_to(root).as_posix()
            result.skipped.append(SkippedFile(source, "read_error"))
            log.warning("folder_unreadable", source=source, error=str(error))
            continue
        for entry in entries:
            path = Path(entry.path)
            source = path.relative_to(root).as_posix()
            if entry.is_dir(follow_symlinks=False):
                if _is_hidden(entry):
                    result.skipped.append(SkippedFile(source + "/", "hidden"))
                else:
                    pending.append(path)
                continue
            if not entry.is_file():
                continue
            reason = _skip_reason(entry)
            if reason is None:
                try:
                    result.files.append(
                        DiscoveredFile(path, source, file_sha256(path), entry.stat().st_size)
                    )
                    continue
                except OSError as error:
                    reason = "read_error"
                    log.warning("file_unreadable", source=source, error=str(error))
            result.skipped.append(SkippedFile(source, reason))

    result.files.sort(key=lambda item: item.source)
    result.skipped.sort(key=lambda item: item.source)
    for skipped in result.skipped:
        if skipped.reason != "read_error":
            log.info("file_skipped", source=skipped.source, reason=skipped.reason)
    return result
