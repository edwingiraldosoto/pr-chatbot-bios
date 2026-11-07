"""Manejadores de Telegram: filtro común, comandos y preguntas (RF-03, RF-06–RF-08, RNF-05).

El orden de evaluación es el de ``specs/contracts/bot-commands.md``:

1. :meth:`BotHandlers.gate` corre antes que todo (grupo -1). Ignora lo que no sea un
   mensaje nuevo en un chat privado, niega el acceso a quien no está autorizado,
   descarta las copias repetidas de un mensaje y aplica el límite por minuto. Si
   rechaza, lanza ``ApplicationHandlerStop`` y ningún otro manejador corre.
2. En el grupo 0, el primer manejador que coincide: comandos conocidos, comando
   desconocido, texto (pregunta) u otro tipo de mensaje.

Los manejadores no conocen la configuración: reciben sus dependencias ya armadas, así las
pruebas los ejercitan con un Telegram falso y sin red.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any, Protocol, cast

from telegram import Bot, Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from mesa_ayuda.answering import Answer
from mesa_ayuda.bot import messages
from mesa_ayuda.bot.messages import split_message
from mesa_ayuda.guards import (
    AccessControl,
    DuplicateFilter,
    RateDecision,
    RateLimiter,
    clean_question,
    is_private_chat,
)
from mesa_ayuda.indexing.indexer import ReindexInProgressError, ReindexSummary, SourceInfo
from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMTimeoutError,
)
from mesa_ayuda.logging_setup import bind_request, get_logger, new_request_id
from mesa_ayuda.storage import Storage, UnansweredReason

log = get_logger(__name__)

Context = ContextTypes.DEFAULT_TYPE
AnyApplication = Application[Any, Any, Any, Any, Any, Any]


TYPING_INTERVAL_SECONDS = 4.0  # Telegram apaga "escribiendo…" a los 5 s


class Answerer(Protocol):
    """Lo que el bot usa de :class:`~mesa_ayuda.answering.AnswerService`."""

    async def answer(self, question: str, history: Sequence[ChatMessage] = ()) -> Answer:
        """Responde una pregunta con fuentes o indica que no se encontró."""
        ...


class DocumentIndex(Protocol):
    """Lo que el bot usa del indexador (:class:`~mesa_ayuda.indexing.indexer.Indexer`)."""

    def sources(self) -> list[SourceInfo]:
        """Documentos conocidos con su estado."""
        ...

    @property
    def is_reindexing(self) -> bool:
        """Si hay una reindexación en curso."""
        ...

    async def reindex_async(self, *, full: bool = False) -> ReindexSummary:
        """Reindexa en un hilo aparte."""
        ...


async def reply(message: Message, text: str) -> None:
    """Envía un texto en tantas partes como haga falta (CA-R5.6)."""
    for part in split_message(text):
        await message.reply_text(part)


def _message(update: Update) -> Message:
    """El mensaje de la actualización; el filtro común garantiza que existe."""
    message = update.message
    if message is None:  # pragma: no cover - el filtro común lo descarta antes
        raise ApplicationHandlerStop
    return message


def _user_id(update: Update) -> int:
    user = update.effective_user
    if user is None:  # pragma: no cover - el filtro común lo descarta antes
        raise ApplicationHandlerStop
    return user.id


class BotHandlers:
    """Manejadores del bot con sus dependencias.

    Args:
        access: Quién puede usar el bot y quién administra.
        limiter: Límite de mensajes por minuto.
        storage: Historial y preguntas sin respuesta.
        indexer: Índice de documentos (``/fuentes`` y ``/reindex``).
        answers: Servicio de respuesta.
        log_questions: ``LOG_QUESTIONS``: incluir el texto de la pregunta en el evento
            ``message_handled`` (CA-R6.2). Con ``LOG_LEVEL=DEBUG`` sale siempre, en un
            evento ``question_text`` aparte.
        typing_interval: Cada cuánto se renueva "escribiendo…".
        duplicates: Filtro de copias repetidas; sin él no se filtran (CA-R5.7).
    """

    def __init__(
        self,
        *,
        access: AccessControl,
        limiter: RateLimiter,
        storage: Storage,
        indexer: DocumentIndex,
        answers: Answerer,
        log_questions: bool = False,
        typing_interval: float = TYPING_INTERVAL_SECONDS,
        duplicates: DuplicateFilter | None = None,
    ) -> None:
        self.access = access
        self.limiter = limiter
        self.storage = storage
        self.indexer = indexer
        self.answers = answers
        self.log_questions = log_questions
        self.typing_interval = typing_interval
        self.duplicates = duplicates or DuplicateFilter(0)
        self._user_locks: dict[int, asyncio.Lock] = {}

    def register(self, application: AnyApplication) -> None:
        """Agrega los manejadores a la aplicación, en el orden del contrato."""
        application.add_handler(TypeHandler(Update, self.gate), group=-1)
        new_message = filters.UpdateType.MESSAGE
        application.add_handler(CommandHandler("start", self.start, filters=new_message))
        application.add_handler(CommandHandler("help", self.help, filters=new_message))
        application.add_handler(CommandHandler("reset", self.reset, filters=new_message))
        application.add_handler(CommandHandler("fuentes", self.sources, filters=new_message))
        application.add_handler(CommandHandler("reindex", self.reindex, filters=new_message))
        application.add_handler(MessageHandler(new_message & filters.COMMAND, self.unknown))
        application.add_handler(MessageHandler(new_message & filters.TEXT, self.question))
        application.add_handler(MessageHandler(new_message, self.not_text))
        application.add_error_handler(self.on_error)

    def _lock(self, user_id: int) -> asyncio.Lock:
        """Candado por usuario: sus mensajes se atienden en orden, uno a la vez."""
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    # --- Filtro común (grupo -1) ------------------------------------------------------------

    async def gate(self, update: Update, context: Context) -> None:
        """Chat privado → acceso → copias → límite (CA-07.2, CA-07.4, CA-R5.7, CA-R5.4)."""
        message, user, chat = update.message, update.effective_user, update.effective_chat
        if message is None or user is None or chat is None:
            raise ApplicationHandlerStop  # ediciones, estados del chat y demás: se ignoran
        if not is_private_chat(chat.type):
            log.info("message_ignored", reason="chat_not_private", chat_type=chat.type)
            raise ApplicationHandlerStop
        bind_request(request_id=new_request_id(), user_id=user.id)
        if not self.access.is_authorized(user.id):
            log.warning("message_handled", outcome="denied")
            await reply(message, messages.access_denied(user.id))
            raise ApplicationHandlerStop
        if self.duplicates.is_duplicate(user.id, message.text):
            log.info("message_handled", outcome="duplicate")
            raise ApplicationHandlerStop  # no responde ni cuenta para el límite
        decision = self.limiter.check(user.id)
        if decision is not RateDecision.ALLOW:
            log.warning("message_handled", outcome="rate_limited", warned=decision.value)
            if decision is RateDecision.WARN:
                await reply(message, messages.RATE_LIMITED)
            raise ApplicationHandlerStop

    # --- Comandos (RF-06) -------------------------------------------------------------------

    async def start(self, update: Update, context: Context) -> None:
        """``/start``: bienvenida (CA-06.1)."""
        await reply(_message(update), messages.START)
        log.info("message_handled", outcome="command", command="start")

    async def help(self, update: Update, context: Context) -> None:
        """``/help``: ayuda según el rol (CA-06.2)."""
        is_admin = self.access.is_admin(_user_id(update))
        await reply(_message(update), messages.help_text(is_admin=is_admin))
        log.info("message_handled", outcome="command", command="help")

    async def reset(self, update: Update, context: Context) -> None:
        """``/reset``: borra el historial de quien lo envía (CA-06.3)."""
        user_id = _user_id(update)
        async with self._lock(user_id):
            deleted = self.storage.reset(user_id)
            await reply(_message(update), messages.RESET_DONE)
        log.info("message_handled", outcome="command", command="reset", deleted=deleted)

    async def sources(self, update: Update, context: Context) -> None:
        """``/fuentes``: documentos indexados con sus fragmentos (CA-06.4)."""
        await reply(_message(update), messages.sources_list(self.indexer.sources()))
        log.info("message_handled", outcome="command", command="fuentes")

    async def reindex(self, update: Update, context: Context) -> None:
        """``/reindex`` y ``/reindex full``, solo para administradores (RF-08).

        Responde de inmediato que empezó y, al terminar, el resumen. Como las
        actualizaciones se procesan en paralelo, el bot sigue atendiendo preguntas mientras
        tanto (CA-08.4).
        """
        message, user_id = _message(update), _user_id(update)
        if not self.access.is_admin(user_id):
            await reply(message, messages.ADMIN_ONLY)  # CA-08.3
            log.info("message_handled", outcome="command", command="reindex", denied=True)
            return
        args = [arg.lower() for arg in context.args or []]
        if args not in ([], ["full"]):
            await reply(message, messages.REINDEX_USAGE)
            return
        full = bool(args)
        if self.indexer.is_reindexing:
            await reply(message, messages.REINDEX_IN_PROGRESS)  # CA-08.5
            log.info("message_handled", outcome="command", command="reindex", busy=True)
            return

        await reply(message, messages.REINDEX_FULL_STARTED if full else messages.REINDEX_STARTED)
        try:
            summary = await self.indexer.reindex_async(full=full)
        except ReindexInProgressError:  # otra empezó entre la revisión y este punto
            await reply(message, messages.REINDEX_IN_PROGRESS)
            return
        except Exception as error:  # el bot sigue vivo; el detalle queda en el log
            log.exception("reindex_failed", full=full)
            await reply(message, messages.reindex_failed(str(error) or type(error).__name__))
            return
        changed = {*summary.added, *summary.updated}
        warnings = [
            (info.source, warning)
            for info in self.indexer.sources()
            if info.source in changed and info.status == "indexed"
            for warning in info.warnings
        ]
        await reply(message, messages.reindex_summary(summary, warnings))
        log.info("message_handled", outcome="command", command="reindex", full=full)

    async def unknown(self, update: Update, context: Context) -> None:
        """Comando que no existe."""
        await reply(_message(update), messages.UNKNOWN_COMMAND)
        log.info("message_handled", outcome="command", command="unknown")

    # --- Preguntas (RF-03, RF-04, RF-05, RNF-05) --------------------------------------------

    async def question(self, update: Update, context: Context) -> None:
        """Texto: pregunta al servicio de respuesta y envía la respuesta con fuentes."""
        message, user_id = _message(update), _user_id(update)
        text = clean_question(message.text)
        if text is None:
            await self.not_text(update, context)
            return
        started = time.perf_counter()
        async with self._lock(user_id):
            async with self._typing(context.bot, message.chat_id):
                outcome, answer_text, answer = await self._answer(user_id, text)
            await reply(message, answer_text)
        log.info(
            "message_handled",
            outcome=outcome,
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=answer.usage.input_tokens if answer else None,
            output_tokens=answer.usage.output_tokens if answer else None,
            llm_calls=answer.usage.llm_calls if answer else None,
            best_score=(
                round(answer.best_score, 4)
                if answer is not None and answer.best_score is not None
                else None
            ),
            sources=[source.label() for source in answer.sources] if answer else [],
            **({"question": text} if self.log_questions else {}),
        )
        log.debug("question_text", question=text)

    async def _answer(self, user_id: int, text: str) -> tuple[str, str, Answer | None]:
        """Resultado para el log, texto para el usuario y la respuesta (si la hubo)."""
        try:
            answer = await self.answers.answer(text, self.storage.history(user_id))
        except LLMTimeoutError as error:
            log.warning("llm_timeout", error=str(error))
            return "error", messages.LLM_TIMEOUT, None
        except (LLMAuthError, LLMBadRequestError) as error:
            # Es un problema de configuración: el operador debe verlo como ERROR.
            log.error("llm_config_error", error_type=type(error).__name__, error=str(error))
            return "error", messages.LLM_UNAVAILABLE, None
        except LLMError as error:
            log.warning("llm_unavailable", error_type=type(error).__name__, error=str(error))
            return "error", messages.LLM_UNAVAILABLE, None

        if answer.found:
            self.storage.add_turn(user_id, text, answer.text)  # sin la línea de fuentes
            return "answered", answer.render(), answer
        self.storage.record_unanswered(
            user_id,
            text,
            rewritten=answer.search_question if answer.search_question != text else None,
            best_score=answer.best_score,
            reason=cast(UnansweredReason, answer.outcome.value),
        )
        return "not_found", messages.NOT_FOUND, answer

    @asynccontextmanager
    async def _typing(self, bot: Bot, chat_id: int) -> AsyncIterator[None]:
        """Muestra "escribiendo…" y lo renueva mientras dura el bloque."""

        async def send() -> None:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramError as error:  # no es motivo para fallar la respuesta
                log.debug("typing_failed", error=str(error))

        async def keep_typing() -> None:
            while True:
                await asyncio.sleep(self.typing_interval)
                await send()

        await send()  # de inmediato, aunque la respuesta tarde poco
        task = asyncio.create_task(keep_typing())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def not_text(self, update: Update, context: Context) -> None:
        """Foto, audio, archivo, sticker o texto vacío (CA-R5.3)."""
        await reply(_message(update), messages.ONLY_TEXT)
        log.info("message_handled", outcome="not_text")

    # --- Errores inesperados ----------------------------------------------------------------

    async def on_error(self, update: object, context: Context) -> None:
        """Registra la traza y avisa al usuario sin mostrarla (RNF-05)."""
        log.error("message_handled", outcome="error", exc_info=context.error)
        if not isinstance(update, Update) or update.message is None:
            return
        chat = update.effective_chat
        if chat is None or not is_private_chat(chat.type):
            return
        try:
            await update.message.reply_text(messages.UNEXPECTED_ERROR)
        except TelegramError as error:
            log.warning("error_reply_failed", error=str(error))
