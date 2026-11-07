"""Pruebas de los manejadores de Telegram con un Telegram falso (T-29 a T-31).

Cada prueba pasa por la ``Application`` real de python-telegram-bot: filtros, grupos y
``ApplicationHandlerStop`` incluidos. Solo el transporte es falso.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mesa_ayuda.bot import messages
from mesa_ayuda.bot.handlers import BotHandlers
from mesa_ayuda.guards import AccessControl, RateLimiter
from mesa_ayuda.indexing.indexer import ReindexSummary, SourceInfo
from mesa_ayuda.storage import Storage
from tests.telegram_fake import TelegramHarness

USER = 111
ADMIN = 999
STRANGER = 555


class FakeIndex:
    """Indexador falso: fuentes fijas y reindexación controlable desde la prueba."""

    def __init__(self, sources: list[SourceInfo] | None = None) -> None:
        self._sources = sources or []
        self.is_reindexing = False
        self.summary = ReindexSummary()

    def sources(self) -> list[SourceInfo]:
        return self._sources

    async def reindex_async(self, *, full: bool = False) -> ReindexSummary:
        return self.summary


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "bot.sqlite3", history_length=6)


@pytest.fixture
def index() -> FakeIndex:
    return FakeIndex(
        [
            SourceInfo("politica_vacaciones.md", "indexed", 4, "", ()),
            SourceInfo("escaneado.pdf", "unreadable", 0, "", ("no tiene texto extraíble",)),
        ]
    )


def make_handlers(storage: Storage, index: FakeIndex, *, limit: int = 10) -> BotHandlers:
    return BotHandlers(
        access=AccessControl(allowed=[USER], admins=[ADMIN]),
        limiter=RateLimiter(limit),
        storage=storage,
        indexer=index,
    )


@pytest.fixture
async def bot(storage: Storage, index: FakeIndex) -> AsyncIterator[TelegramHarness]:
    async with TelegramHarness(make_handlers(storage, index)) as harness:
        yield harness


# --- Comandos (T-29; RF-06) -----------------------------------------------------------------


async def test_start_welcomes(bot: TelegramHarness) -> None:
    # CA-06.1
    assert await bot.send(USER, "/start") == [messages.START]


async def test_help_shows_admin_commands_only_to_admins(bot: TelegramHarness) -> None:
    # CA-06.2
    assert await bot.send(USER, "/help") == [messages.help_text(is_admin=False)]
    assert await bot.send(ADMIN, "/help") == [messages.help_text(is_admin=True)]


async def test_reset_deletes_only_the_sender_history(
    bot: TelegramHarness, storage: Storage
) -> None:
    # CA-06.3
    storage.add_turn(USER, "p", "r")
    storage.add_turn(ADMIN, "p", "r")

    assert await bot.send(USER, "/reset") == ["Listo, borré nuestra conversación."]
    assert storage.history(USER) == []
    assert len(storage.history(ADMIN)) == 2


async def test_sources_lists_indexed_documents(bot: TelegramHarness) -> None:
    # CA-06.4
    assert await bot.send(USER, "/fuentes") == [
        "Documentos que puedo consultar (1):\n1. politica_vacaciones.md (4 fragmentos)"
    ]


async def test_sources_without_documents(storage: Storage) -> None:
    async with TelegramHarness(make_handlers(storage, FakeIndex())) as bot:
        assert await bot.send(USER, "/fuentes") == ["No hay documentos indexados."]


async def test_command_addressed_to_the_bot_by_name(bot: TelegramHarness) -> None:
    assert await bot.send(USER, "/start@mesa_bot") == [messages.START]


async def test_unknown_command(bot: TelegramHarness) -> None:
    assert await bot.send(USER, "/vacaciones") == [messages.UNKNOWN_COMMAND]


# --- Filtro común (T-29; RF-07, CA-R5.4) ----------------------------------------------------


async def test_stranger_gets_their_id_and_nothing_else_runs(
    bot: TelegramHarness, storage: Storage
) -> None:
    # CA-07.2
    storage.add_turn(STRANGER, "p", "r")

    replies = await bot.send(STRANGER, "/reset")

    assert replies == [messages.access_denied(STRANGER)]
    assert len(storage.history(STRANGER)) == 2  # /reset no corrió


async def test_empty_access_lists_deny_everyone(storage: Storage, index: FakeIndex) -> None:
    # CA-07.3
    handlers = BotHandlers(
        access=AccessControl(allowed=[], admins=[]),
        limiter=RateLimiter(10),
        storage=storage,
        indexer=index,
    )
    async with TelegramHarness(handlers) as bot:
        assert await bot.send(USER, "/start") == [messages.access_denied(USER)]


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_group_chats_are_ignored_even_for_authorized_users(
    bot: TelegramHarness, chat_type: str
) -> None:
    # CA-07.4
    assert await bot.send(USER, "/start", chat_type=chat_type) == []
    assert bot.sent() == []


async def test_edited_messages_are_ignored(bot: TelegramHarness) -> None:
    assert await bot.send(USER, "/start", edited=True) == []


async def test_rate_limit_warns_once_and_drops_the_rest(storage: Storage, index: FakeIndex) -> None:
    # CA-R5.4: con límite 2, el tercero recibe el aviso y el cuarto nada.
    async with TelegramHarness(make_handlers(storage, index, limit=2)) as bot:
        replies = [await bot.send(USER, "/start") for _ in range(4)]

    assert replies == [[messages.START], [messages.START], [messages.RATE_LIMITED], []]


async def test_rate_limit_is_per_user(storage: Storage, index: FakeIndex) -> None:
    async with TelegramHarness(make_handlers(storage, index, limit=1)) as bot:
        await bot.send(USER, "/start")
        assert await bot.send(USER, "/start") == [messages.RATE_LIMITED]
        assert await bot.send(ADMIN, "/start") == [messages.START]
