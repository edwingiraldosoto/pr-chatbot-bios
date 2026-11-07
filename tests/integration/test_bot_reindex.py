"""``/reindex`` y ``/fuentes`` con el indexador real (T-31; RF-08, quickstart 11 a 13).

El bot y el índice son los de producción (Chroma en disco, manifest); solo el modelo de
embeddings (``FakeEmbedder``) y el transporte de Telegram son falsos.
"""

from __future__ import annotations

from pathlib import Path

from mesa_ayuda.bot import messages
from mesa_ayuda.bot.handlers import BotHandlers
from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.guards import AccessControl, RateLimiter
from mesa_ayuda.indexing.indexer import Indexer
from mesa_ayuda.storage import Storage
from tests.telegram_fake import TelegramHarness
from tests.unit.answers_fake import ScriptedAnswers

ADMIN = 999


async def test_add_change_and_delete_a_document_from_telegram(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge_base"
    knowledge.mkdir()
    (knowledge / "vacaciones.md").write_text("# Días\n\nSon 15 días hábiles.", encoding="utf-8")
    indexer = Indexer(
        knowledge_base=knowledge,
        data_dir=tmp_path / "data",
        embedder=FakeEmbedder(),
        chunk_size=400,
        chunk_overlap=50,
    )
    indexer.reindex()  # indexación inicial, como al arrancar
    handlers = BotHandlers(
        access=AccessControl(allowed=[], admins=[ADMIN]),
        limiter=RateLimiter(100),
        storage=Storage(tmp_path / "data" / "bot.sqlite3", history_length=6),
        indexer=indexer,
        answers=ScriptedAnswers(),
    )

    async with TelegramHarness(handlers) as bot:
        # 11: documento nuevo.
        (knowledge / "vpn.md").write_text("# VPN\n\nUsa FortiClient.", encoding="utf-8")
        added = await bot.send(ADMIN, "/reindex")
        listed = await bot.send(ADMIN, "/fuentes")

        # 12: documento modificado.
        (knowledge / "vpn.md").write_text("# VPN\n\nUsa GlobalProtect.", encoding="utf-8")
        updated = await bot.send(ADMIN, "/reindex")

        # 13: documento borrado.
        (knowledge / "vpn.md").unlink()
        removed = await bot.send(ADMIN, "/reindex")
        after = await bot.send(ADMIN, "/fuentes")

    assert added[0] == messages.REINDEX_STARTED
    assert ": 1 nuevo, 0 actualizados, 0 eliminados, 1 sin cambios." in added[1]
    assert listed == [
        "Documentos que puedo consultar (2):\n"
        "1. vacaciones.md (1 fragmento)\n"
        "2. vpn.md (1 fragmento)"
    ]
    assert ": 0 nuevos, 1 actualizado, 0 eliminados, 1 sin cambios." in updated[1]
    assert ": 0 nuevos, 0 actualizados, 1 eliminado, 1 sin cambios." in removed[1]
    assert after == ["Documentos que puedo consultar (1):\n1. vacaciones.md (1 fragmento)"]
    hits = indexer.search(FakeEmbedder().embed_query("VPN GlobalProtect"), 5)
    assert all(hit.source == "vacaciones.md" for hit in hits)  # el borrado no se cita


async def test_full_rebuild_from_telegram(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge_base"
    knowledge.mkdir()
    (knowledge / "a.md").write_text("Texto A.", encoding="utf-8")
    (knowledge / "~$temporal.docx").write_text("x", encoding="utf-8")
    indexer = Indexer(
        knowledge_base=knowledge,
        data_dir=tmp_path / "data",
        embedder=FakeEmbedder(),
        chunk_size=400,
        chunk_overlap=50,
    )
    indexer.reindex()
    handlers = BotHandlers(
        access=AccessControl(allowed=[], admins=[ADMIN]),
        limiter=RateLimiter(100),
        storage=Storage(tmp_path / "bot.sqlite3", history_length=6),
        indexer=indexer,
        answers=ScriptedAnswers(),
    )

    async with TelegramHarness(handlers) as bot:
        replies = await bot.send(ADMIN, "/reindex full")

    assert replies[0] == messages.REINDEX_FULL_STARTED
    assert "(motivo: solicitada): 1 documento indexado." in replies[1]
    assert "Ignorados: 1 archivo" in replies[1]
