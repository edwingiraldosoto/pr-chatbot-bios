"""Pruebas del manifest (T-14; RF-09, CA-09.1, CA-09.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mesa_ayuda.indexing.manifest import (
    DocumentEntry,
    Manifest,
    diff,
    load_manifest,
    save_manifest,
)
from mesa_ayuda.ingest.discovery import DiscoveredFile


def found(source: str, sha256: str) -> DiscoveredFile:
    return DiscoveredFile(path=Path(source), source=source, sha256=sha256, size=1)


def entry(sha256: str, version: int = 1, status: str = "indexed") -> DocumentEntry:
    return DocumentEntry(hash=sha256, version=version, status=status, chunks=3)  # type: ignore[arg-type]


def test_diff_classifies_by_content_hash() -> None:
    manifest = Manifest(
        settings={},
        collection="kb_1",
        documents={"a.md": entry("h1"), "b.md": entry("h2"), "c.md": entry("h3")},
    )

    changes = diff(
        manifest, [found("a.md", "h1"), found("b.md", "h2-cambiado"), found("d.md", "h4")]
    )

    assert changes.new == ["d.md"]
    assert changes.changed == ["b.md"]
    assert changes.unchanged == ["a.md"]
    assert changes.deleted == ["c.md"]
    assert changes.has_changes


def test_rename_is_delete_plus_new() -> None:
    # CA-09.4: la ruta es la identidad del documento (y lo que se cita).
    manifest = Manifest(settings={}, collection="kb_1", documents={"viejo.md": entry("h1")})

    changes = diff(manifest, [found("nuevo.md", "h1")])

    assert (changes.new, changes.deleted) == (["nuevo.md"], ["viejo.md"])


def test_unchanged_unreadable_document_is_not_retried() -> None:
    manifest = Manifest(
        settings={}, collection="kb_1", documents={"roto.pdf": entry("h1", status="unreadable")}
    )

    changes = diff(manifest, [found("roto.pdf", "h1")])

    assert changes.unchanged == ["roto.pdf"] and not changes.has_changes


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data" / "manifest.json"
    manifest = Manifest(
        settings={"embedding_model": "m", "chunk_size": 800, "chunk_overlap": 150},
        collection="kb_2",
        documents={
            "política.md": DocumentEntry("sha256:a", 2, "indexed", 5, "2026-11-06T10:00:00-05:00"),
            "roto.pdf": DocumentEntry("sha256:b", 0, "unreadable", warnings=["PDF dañado"]),
        },
    )

    save_manifest(path, manifest)
    loaded = load_manifest(path)

    assert loaded == manifest
    assert "política.md" in path.read_text(encoding="utf-8")  # legible, sin í
    assert not path.with_name("manifest.json.tmp").exists()
    assert loaded is not None and loaded.active_doc_keys() == {"política.md": "política.md@2"}


def test_missing_manifest_returns_none(tmp_path: Path) -> None:
    assert load_manifest(tmp_path / "manifest.json") is None


@pytest.mark.parametrize(
    "content",
    [
        "{no es json",
        json.dumps({"schema_version": 99, "settings": {}, "collection": "kb_1", "documents": {}}),
        json.dumps({"schema_version": 1, "settings": {}, "collection": "kb_1"}),
        json.dumps(
            {
                "schema_version": 1,
                "settings": {},
                "collection": "kb_1",
                "documents": {"a.md": {"hash": "h", "version": 1, "status": "raro"}},
            }
        ),
    ],
)
def test_corrupt_manifest_returns_none(tmp_path: Path, content: str) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(content, encoding="utf-8")

    assert load_manifest(path) is None


def test_failed_write_keeps_previous_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "manifest.json"
    original = Manifest(settings={}, collection="kb_1", documents={"a.md": entry("h1")})
    save_manifest(path, original)

    def broken_dump(*_: object, **__: object) -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(json, "dump", broken_dump)
    with pytest.raises(OSError, match="disco lleno"):
        save_manifest(path, Manifest(settings={}, collection="kb_9"))

    assert load_manifest(path) == original
