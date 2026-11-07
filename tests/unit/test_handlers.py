"""Pruebas de los manejadores de Telegram con un Telegram falso (T-29 a T-31).

Cada prueba pasa por la ``Application`` real de python-telegram-bot: filtros, grupos y
``ApplicationHandlerStop`` incluidos. Solo el transporte es falso.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from mesa_ayuda.answering import Answer, Outcome, Source
from mesa_ayuda.bot import messages
from mesa_ayuda.bot.handlers import Answerer, BotHandlers
from mesa_ayuda.bot.small_talk import SmallTalk
from mesa_ayuda.embeddings import EmbeddingModelError
from mesa_ayuda.guards import AccessControl, DuplicateFilter, RateLimiter
from mesa_ayuda.indexing.indexer import ReindexInProgressError, ReindexSummary, SourceInfo
from mesa_ayuda.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from mesa_ayuda.logging_setup import clear_request, configure_logging
from mesa_ayuda.storage import Storage
from tests.telegram_fake import TelegramHarness
from tests.unit.answers_fake import ScriptedAnswers, answered, not_found

USER = 111
ADMIN = 999
STRANGER = 555


class FakeIndex:
    """Indexador falso: fuentes fijas y reindexación controlable desde la prueba."""

    def __init__(self, sources: list[SourceInfo] | None = None) -> None:
        self._sources = sources or []
        self.is_reindexing = False
        self.summary: ReindexSummary | BaseException = ReindexSummary()
        self.release: asyncio.Event | None = None  # si existe, la reindexación lo espera
        self.calls: list[bool] = []

    def sources(self) -> list[SourceInfo]:
        return self._sources

    async def reindex_async(self, *, full: bool = False) -> ReindexSummary:
        if self.is_reindexing:
            raise ReindexInProgressError
        self.is_reindexing = True
        self.calls.append(full)
        try:
            if self.release is not None:
                await self.release.wait()
            if isinstance(self.summary, BaseException):
                raise self.summary
            return self.summary
        finally:
            self.is_reindexing = False


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


def make_handlers(
    storage: Storage,
    index: FakeIndex,
    *,
    limit: int = 10,
    answers: Answerer | None = None,
    log_questions: bool = False,
    typing_interval: float = 4.0,
) -> BotHandlers:
    return BotHandlers(
        access=AccessControl(allowed=[USER], admins=[ADMIN]),
        limiter=RateLimiter(limit),
        storage=storage,
        indexer=index,
        answers=answers or ScriptedAnswers(),
        log_questions=log_questions,
        typing_interval=typing_interval,
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
        answers=ScriptedAnswers(),
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


# --- Preguntas (T-30; RF-03, RF-05, RF-10, RNF-05) ------------------------------------------


async def test_answer_ends_with_its_sources_and_is_stored_without_them(
    storage: Storage, index: FakeIndex
) -> None:
    # CA-03.1; el historial guarda la respuesta sin la línea de fuentes.
    script = ScriptedAnswers([answered("Tienes 15 días hábiles.", Source("vac.md", "Días"))])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        replies = await bot.send(USER, "  ¿Cuántos días de vacaciones tengo?  ")

    assert replies == ['Tienes 15 días hábiles.\n\nFuentes: vac.md, sección "Días".']
    assert script.calls[0][0] == "¿Cuántos días de vacaciones tengo?"
    assert [m.content for m in storage.history(USER)] == [
        "¿Cuántos días de vacaciones tengo?",
        "Tienes 15 días hábiles.",
    ]
    assert "typing" in bot.actions()


async def test_follow_up_receives_the_user_history(storage: Storage, index: FakeIndex) -> None:
    # RF-05: la segunda pregunta llega con el turno anterior; la de otro usuario no.
    script = ScriptedAnswers([answered("15 días."), answered("Hasta dos periodos.")])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        await bot.send(USER, "¿Cuántos días de vacaciones?")
        await bot.send(USER, "¿y se acumulan?")
        await bot.send(ADMIN, "otra cosa")

    assert script.calls[0][1] == []
    assert [m.content for m in script.calls[1][1]] == ["¿Cuántos días de vacaciones?", "15 días."]
    assert script.calls[2][1] == []  # CA-05.3


async def test_below_threshold_is_recorded_and_not_kept_in_history(
    storage: Storage, index: FakeIndex
) -> None:
    # CA-04.1, CA-10.1
    script = ScriptedAnswers([not_found(best_score=0.7581)])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        replies = await bot.send(USER, "¿Cuál es la capital de Australia?")

    assert replies == [messages.NOT_FOUND]
    assert storage.history(USER) == []
    (row,) = storage.unanswered()
    assert (row.user_id, row.question, row.rewritten, row.best_score, row.reason) == (
        USER,
        "¿Cuál es la capital de Australia?",
        None,  # igual a la original: no se repite
        0.7581,
        "below_threshold",
    )


async def test_model_not_found_records_the_rewritten_question(
    storage: Storage, index: FakeIndex
) -> None:
    script = ScriptedAnswers(
        [not_found(Outcome.LLM_NOT_FOUND, search_question="¿Cuál es el salario del gerente?")]
    )
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        assert await bot.send(USER, "¿y el del gerente?") == [messages.NOT_FOUND]

    (row,) = storage.unanswered()
    assert (row.rewritten, row.reason) == ("¿Cuál es el salario del gerente?", "llm_not_found")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMTimeoutError("30 s"), messages.LLM_TIMEOUT),  # CA-R5.2
        (LLMUnavailableError("503"), messages.LLM_UNAVAILABLE),  # CA-R5.1
        (LLMAuthError("401"), messages.LLM_UNAVAILABLE),
        (LLMBadRequestError("404 modelo"), messages.LLM_UNAVAILABLE),
    ],
)
async def test_llm_errors_become_clear_messages(
    storage: Storage, index: FakeIndex, error: Exception, expected: str
) -> None:
    handlers = make_handlers(storage, index, answers=ScriptedAnswers([error]))
    async with TelegramHarness(handlers) as bot:
        assert await bot.send(USER, "¿Cuántos días?") == [expected]

    assert storage.history(USER) == []
    assert storage.unanswered() == []


async def test_configuration_errors_are_logged_as_errors(
    storage: Storage, index: FakeIndex
) -> None:
    script = ScriptedAnswers([LLMAuthError("401 invalid x-api-key")])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        with capture_logs() as logs:
            await bot.send(USER, "¿Cuántos días?")

    (event,) = [e for e in logs if e["event"] == "llm_config_error"]
    assert event["log_level"] == "error"
    assert event["error_type"] == "LLMAuthError"


async def test_unexpected_error_shows_no_trace(storage: Storage, index: FakeIndex) -> None:
    script = ScriptedAnswers([RuntimeError("detalle interno /ruta/secreta.py")])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        with capture_logs() as logs:
            replies = await bot.send(USER, "¿Cuántos días?")

    assert replies == [messages.UNEXPECTED_ERROR]
    assert any(e.get("outcome") == "error" and e["log_level"] == "error" for e in logs)


FILE = {"file_id": "f", "file_unique_id": "u"}


@pytest.mark.parametrize(
    "content",
    [
        {"photo": [{**FILE, "width": 1, "height": 1}]},
        {"voice": {**FILE, "duration": 3}},
        {
            "sticker": {
                **FILE,
                "width": 1,
                "height": 1,
                "is_animated": False,
                "is_video": False,
                "type": "regular",
            }
        },
        {"document": {**FILE, "file_name": "a.pdf"}},
    ],
    ids=["foto", "audio", "sticker", "archivo"],
)
async def test_non_text_messages_suggest_dictation(
    bot: TelegramHarness, content: dict[str, object]
) -> None:
    # CA-R5.3
    assert await bot.send(USER, **content) == [messages.ONLY_TEXT]


async def test_blank_text_is_treated_as_no_text(storage: Storage, index: FakeIndex) -> None:
    script = ScriptedAnswers()
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        assert await bot.send(USER, " \n ") == [messages.ONLY_TEXT]

    assert script.calls == []


async def test_long_answer_is_sent_in_parts_with_sources_last(
    storage: Storage, index: FakeIndex
) -> None:
    # CA-R5.6
    long_text = "\n\n".join(f"Paso {n}." + " detalle" * 120 for n in range(1, 11))
    script = ScriptedAnswers([answered(long_text, Source("guia.pdf", page=3))])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        replies = await bot.send(USER, "¿Cómo configuro la VPN?")

    assert len(replies) > 1
    assert all(len(part) <= 4096 for part in replies)
    assert replies[-1].endswith("Fuentes: guia.pdf, página 3.")
    assert sum("Fuentes:" in part for part in replies) == 1


@pytest.mark.parametrize("enabled", [False, True])
async def test_question_text_is_only_logged_when_enabled(
    storage: Storage, index: FakeIndex, enabled: bool
) -> None:
    # CA-R6.1, CA-R6.2
    question = "¿Cuánto gana el gerente?"
    async with TelegramHarness(make_handlers(storage, index, log_questions=enabled)) as bot:
        with capture_logs() as logs:
            await bot.send(USER, question)

    (event,) = [e for e in logs if e["event"] == "message_handled"]
    assert event["outcome"] == "answered"
    assert {"latency_ms", "input_tokens", "output_tokens", "best_score"} <= set(event)
    assert event["sources"] == ['politica_vacaciones.md, sección "Días"']
    assert ("question" in event) is enabled
    # Fuera de DEBUG, el texto solo aparece si se habilitó.
    visible = [e for e in logs if e["log_level"] != "debug" and question in str(e)]
    assert bool(visible) is enabled


async def test_every_event_of_a_message_carries_request_and_user(
    storage: Storage, index: FakeIndex
) -> None:
    # CA-R6.1, con la configuración real de logs (JSON por línea).
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    try:
        async with TelegramHarness(make_handlers(storage, index)) as bot:
            await bot.send(USER, "¿Cuánto gana el gerente?")
            await bot.send(STRANGER, "hola")
    finally:
        clear_request()
        logging.getLogger().handlers.clear()

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    handled = [e for e in events if e["event"] == "message_handled"]
    assert [(e["outcome"], e["user_id"]) for e in handled] == [
        ("answered", USER),
        ("denied", STRANGER),
    ]
    assert handled[0]["request_id"] != handled[1]["request_id"]
    assert "gerente" not in stream.getvalue()  # CA-R6.2 con LOG_LEVEL=INFO


async def test_typing_is_renewed_while_the_answer_takes_long(
    storage: Storage, index: FakeIndex
) -> None:
    script = ScriptedAnswers(delay=0.12)
    handlers = make_handlers(storage, index, answers=script, typing_interval=0.03)
    async with TelegramHarness(handlers) as bot:
        await bot.send(USER, "¿Cuántos días?")
        sent_after_answer = len(bot.actions())
        await asyncio.sleep(0.1)

    assert sent_after_answer >= 3
    assert len(bot.actions()) == sent_after_answer  # se detiene al responder


async def test_messages_of_one_user_wait_their_turn_others_do_not(
    storage: Storage, index: FakeIndex
) -> None:
    order: list[str] = []

    async def slow(question: str) -> Answer:
        order.append(f"inicio {question}")
        await asyncio.sleep(0.05)
        order.append(f"fin {question}")
        return answered("ok", question=question)

    script = ScriptedAnswers([slow, slow, slow])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        await asyncio.gather(bot.send(USER, "a"), bot.send(USER, "b"), bot.send(ADMIN, "c"))

    assert order.index("fin a") < order.index("inicio b")  # el mismo usuario, en orden
    assert order.index("inicio c") < order.index("fin a")  # otro usuario no espera


# --- /reindex (T-31; RF-08) -----------------------------------------------------------------


async def test_reindex_answers_started_and_then_the_summary(
    bot: TelegramHarness, index: FakeIndex
) -> None:
    # CA-08.1
    index.summary = ReindexSummary(added=["nuevo.md"], unchanged=3, duration_s=1.5)

    replies = await bot.send(ADMIN, "/reindex")

    assert replies == [
        messages.REINDEX_STARTED,
        "Reindexación terminada en 1,5 s: 1 nuevo, 0 actualizados, 0 eliminados, 3 sin cambios.",
    ]
    assert index.calls == [False]


async def test_reindex_full_rebuilds(bot: TelegramHarness, index: FakeIndex) -> None:
    # CA-08.2
    index.summary = ReindexSummary(added=["a.md"], full_rebuild=True, rebuild_reason="solicitada")

    replies = await bot.send(ADMIN, "/reindex FULL")

    assert replies[0] == messages.REINDEX_FULL_STARTED
    assert replies[1].startswith("Reconstrucción completa terminada")
    assert index.calls == [True]


async def test_reindex_is_only_for_admins(bot: TelegramHarness, index: FakeIndex) -> None:
    # CA-08.3
    assert await bot.send(USER, "/reindex") == [messages.ADMIN_ONLY]
    assert index.calls == []


async def test_reindex_with_an_unknown_argument_shows_the_usage(
    bot: TelegramHarness, index: FakeIndex
) -> None:
    assert await bot.send(ADMIN, "/reindex todo") == [messages.REINDEX_USAGE]
    assert index.calls == []


async def test_bot_keeps_answering_during_a_reindex_and_rejects_a_second_one(
    storage: Storage, index: FakeIndex
) -> None:
    # CA-08.4, CA-08.5
    index.release = asyncio.Event()
    script = ScriptedAnswers([answered("15 días.")])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        running = asyncio.create_task(bot.send(ADMIN, "/reindex"))
        while not index.is_reindexing:
            await asyncio.sleep(0)

        question = await bot.send(USER, "¿Cuántos días de vacaciones?")
        second = await bot.send(ADMIN, "/reindex full")
        sources = await bot.send(USER, "/fuentes")
        index.release.set()
        await running
        admin_chat = bot.sent(ADMIN)

    assert question[0].startswith("15 días.")
    assert second == [messages.REINDEX_IN_PROGRESS]
    assert sources[0].startswith("Documentos que puedo consultar")
    started, busy, summary = admin_chat
    assert (started, busy) == (messages.REINDEX_STARTED, messages.REINDEX_IN_PROGRESS)
    assert summary.startswith("Reindexación terminada")
    assert index.calls == [False]


async def test_reindex_failure_is_reported_and_the_bot_survives(
    bot: TelegramHarness, index: FakeIndex
) -> None:
    index.summary = EmbeddingModelError("no se pudo descargar el modelo de embeddings")

    replies = await bot.send(ADMIN, "/reindex")

    assert replies[1] == (
        "La reindexación falló: no se pudo descargar el modelo de embeddings. "
        "Revisa el log del bot."
    )
    assert await bot.send(ADMIN, "/start") == [messages.START]


async def test_summary_includes_warnings_of_new_or_updated_documents(storage: Storage) -> None:
    # CA-R5.5 y advertencias de texto sospechoso (RF-13).
    index = FakeIndex(
        [
            SourceInfo("nuevo.pdf", "indexed", 2, "", ("página 3: texto sospechoso",)),
            SourceInfo("viejo.pdf", "indexed", 2, "", ("página 1: ya avisado",)),
        ]
    )
    index.summary = ReindexSummary(
        added=["nuevo.pdf"], unreadable=[("escaneado.pdf", "no tiene texto extraíble")]
    )
    async with TelegramHarness(make_handlers(storage, index)) as bot:
        replies = await bot.send(ADMIN, "/reindex")

    assert "Ilegibles: escaneado.pdf (no tiene texto extraíble)." in replies[1]
    assert "Advertencias: nuevo.pdf: página 3: texto sospechoso." in replies[1]
    assert "viejo.pdf" not in replies[1]


# --- Copias repetidas (T-49; CA-R5.7) -------------------------------------------------------


async def test_burst_of_copies_gets_one_answer_and_one_llm_call(
    storage: Storage, index: FakeIndex
) -> None:
    # Lo visto en la prueba real: un mensaje llegó 10 veces y el bot respondió 10.
    script = ScriptedAnswers()
    handlers = make_handlers(storage, index, answers=script, limit=10)
    handlers.duplicates = DuplicateFilter(5)
    async with TelegramHarness(handlers) as bot:
        replies = [await bot.send(USER, "¿Cómo pido vacaciones?") for _ in range(10)]
        with capture_logs() as logs:
            await bot.send(USER, "¿Cómo pido vacaciones?")
        after = await bot.send(USER, "¿Cuántos días de vacaciones?")
        starts = [await bot.send(USER, "/start") for _ in range(3)]

    assert replies[0] == ['Respuesta.\n\nFuentes: politica_vacaciones.md, sección "Días".']
    assert replies[1:] == [[]] * 9
    assert [question for question, _ in script.calls] == [
        "¿Cómo pido vacaciones?",
        "¿Cuántos días de vacaciones?",
    ]
    assert any(e.get("outcome") == "duplicate" for e in logs)
    assert after[0].startswith("Respuesta.")  # las copias no gastaron el límite por minuto
    assert starts == [[messages.START], [], []]


async def test_duplicate_filter_runs_after_the_access_check(
    storage: Storage, index: FakeIndex
) -> None:
    handlers = make_handlers(storage, index)
    handlers.duplicates = DuplicateFilter(5)
    async with TelegramHarness(handlers) as bot:
        first = await bot.send(STRANGER, "hola")
        second = await bot.send(STRANGER, "hola")

    assert first == [messages.access_denied(STRANGER)]
    assert second == [messages.access_denied(STRANGER)]  # sigue sabiendo que no tiene acceso


# --- Saludos (T-50; CA-06.5) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("hola", SmallTalk.GREETING),
        ("¡Holaaa!", SmallTalk.GREETING),
        ("Buenas tardes", SmallTalk.GREETING),
        ("muchas gracias", SmallTalk.THANKS),
        ("chao", SmallTalk.FAREWELL),
    ],
)
async def test_small_talk_is_answered_cordially_without_the_model(
    storage: Storage, index: FakeIndex, text: str, kind: SmallTalk
) -> None:
    script = ScriptedAnswers()
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        with capture_logs() as logs:
            replies = await bot.send(USER, text)

    assert replies == [messages.small_talk(kind, "Usuario")]
    assert script.calls == []  # sin búsqueda ni modelo
    assert storage.history(USER) == [] and storage.unanswered() == []
    assert any(e.get("outcome") == "small_talk" and e["kind"] == kind.value for e in logs)


async def test_greeting_uses_the_first_name_and_mentions_help(bot: TelegramHarness) -> None:
    (reply,) = await bot.send(USER, "hola")

    assert reply.startswith("Hola, Usuario.") and "/help" in reply


async def test_greeting_with_a_question_is_a_question(storage: Storage, index: FakeIndex) -> None:
    script = ScriptedAnswers([answered("Tienes 15 días hábiles.")])
    async with TelegramHarness(make_handlers(storage, index, answers=script)) as bot:
        replies = await bot.send(USER, "Hola, ¿cuántos días de vacaciones tengo?")

    assert replies[0].startswith("Tienes 15 días hábiles.")
    assert script.calls[0][0] == "Hola, ¿cuántos días de vacaciones tengo?"
