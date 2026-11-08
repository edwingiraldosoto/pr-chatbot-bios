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
import hashlib
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, tzinfo
from datetime import time as day_time
from pathlib import Path
from typing import Any, Protocol, cast

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from mesa_ayuda.answering import Answer
from mesa_ayuda.bot import messages
from mesa_ayuda.bot.messages import split_message
from mesa_ayuda.bot.small_talk import classify
from mesa_ayuda.guards import (
    AccessControl,
    DuplicateFilter,
    RateDecision,
    RateLimiter,
    clean_question,
    invite_fingerprint,
    invite_matches,
    invite_payload,
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
from mesa_ayuda.storage import Quota, Storage, UnansweredReason

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


async def reply(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Envía un texto en tantas partes como haga falta (CA-R5.6); botones en la última."""
    parts = split_message(text)
    for number, part in enumerate(parts, start=1):
        await message.reply_text(part, reply_markup=markup if number == len(parts) else None)


DOWNLOAD_PREFIX = "doc:"


def document_id(source: str) -> str:
    """Identificador del botón de descarga: huella de la ruta, no la ruta (CA-14.2)."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


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
        invite_code: ``INVITE_CODE``; sin él no hay invitaciones (CA-07.5).
        timezone: Zona horaria del día de los topes y de ``/invitados``.
        knowledge_base: ``KNOWLEDGE_BASE_PATH``, de donde salen los archivos que se
            descargan con el botón de fuente.
        source_download: ``bot.source_download``: botón "Descargar" por fuente (RF-14).
        notify_admins: ``REINDEX_NOTIFY_ADMINS``: avisar a los administradores el
            resultado de la reindexación programada si hubo cambios o errores.
        daily_questions_per_user: Tope diario por usuario (CA-R8.1); ``None`` = sin tope.
        daily_questions_total: Tope diario de todos (CA-R8.2); ``None`` = sin tope.
        max_question_chars: Largo máximo de una pregunta (CA-R8.3); ``None`` = sin
            límite.
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
        invite_code: str | None = None,
        timezone: tzinfo = UTC,
        daily_questions_per_user: int | None = None,
        daily_questions_total: int | None = None,
        max_question_chars: int | None = None,
        notify_admins: bool = True,
        knowledge_base: Path | None = None,
        source_download: bool = False,
    ) -> None:
        self.access = access
        self.limiter = limiter
        self.storage = storage
        self.indexer = indexer
        self.answers = answers
        self.log_questions = log_questions
        self.typing_interval = typing_interval
        self.duplicates = duplicates or DuplicateFilter(0)
        self.invite_code = invite_code
        self._invite_hash = invite_fingerprint(invite_code) if invite_code else None
        self.timezone = timezone
        self.daily_questions_per_user = daily_questions_per_user
        self.daily_questions_total = daily_questions_total
        self.max_question_chars = max_question_chars
        self.notify_admins = notify_admins
        self.knowledge_base = knowledge_base
        self.source_download = source_download and knowledge_base is not None
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
        application.add_handler(CommandHandler("invitacion", self.invitation, filters=new_message))
        application.add_handler(CommandHandler("invitados", self.invited, filters=new_message))
        application.add_handler(CommandHandler("revocar", self.revoke, filters=new_message))
        application.add_handler(MessageHandler(new_message & filters.COMMAND, self.unknown))
        application.add_handler(MessageHandler(new_message & filters.TEXT, self.question))
        application.add_handler(MessageHandler(new_message, self.not_text))
        application.add_handler(CallbackQueryHandler(self.download, pattern=f"^{DOWNLOAD_PREFIX}"))
        application.add_error_handler(self.on_error)

    def today(self) -> str:
        """Día actual (``AAAA-MM-DD``) en la zona horaria del bot."""
        return datetime.now(self.timezone).date().isoformat()

    def is_authorized(self, user_id: int) -> bool:
        """Autorizado por configuración o invitado con el código vigente (CA-07.1, CA-07.5)."""
        if self.access.is_authorized(user_id):
            return True
        return self._invite_hash is not None and self.storage.is_invited(user_id, self._invite_hash)

    def _lock(self, user_id: int) -> asyncio.Lock:
        """Candado por usuario: sus mensajes se atienden en orden, uno a la vez."""
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    # --- Filtro común (grupo -1) ------------------------------------------------------------

    async def gate(self, update: Update, context: Context) -> None:
        """Chat privado → acceso → copias → límite (CA-07.2, CA-07.4, CA-R5.7, CA-R5.4)."""
        message, user, chat = update.message, update.effective_user, update.effective_chat
        query = update.callback_query  # botón de descarga (RF-14)
        if (message is None and query is None) or user is None or chat is None:
            raise ApplicationHandlerStop  # ediciones, estados del chat y demás: se ignoran
        if not is_private_chat(chat.type):
            log.info("message_ignored", reason="chat_not_private", chat_type=chat.type)
            raise ApplicationHandlerStop
        bind_request(request_id=new_request_id(), user_id=user.id)
        if message is None:
            assert query is not None
            await self._gate_button(query, user.id)
            return
        if not self.is_authorized(user.id):
            payload = invite_payload(message.text)
            if (
                payload is not None
                and self.invite_code is not None
                and self._invite_hash is not None
                and invite_matches(payload, self.invite_code)
            ):
                self.storage.invite(
                    user.id, first_name=user.first_name or "", code_hash=self._invite_hash
                )
                log.info("message_handled", outcome="invited")
                await reply(message, messages.START)
                raise ApplicationHandlerStop
            log.warning("message_handled", outcome="denied", invite_attempt=payload is not None)
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

    async def _gate_button(self, query: Any, user_id: int) -> None:
        """Acceso y límite por minuto para los botones (sin copias: no traen texto)."""
        if not self.is_authorized(user_id):
            log.warning("message_handled", outcome="denied", button=True)
            await query.answer(messages.access_denied(user_id), show_alert=True)
            raise ApplicationHandlerStop
        decision = self.limiter.check(user_id)
        if decision is not RateDecision.ALLOW:
            log.warning("message_handled", outcome="rate_limited", button=True)
            await query.answer(messages.RATE_LIMITED if decision is RateDecision.WARN else None)
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
        await reply(message, messages.reindex_summary(summary, self._new_warnings(summary)))
        log.info("message_handled", outcome="command", command="reindex", full=full)

    def _new_warnings(self, summary: ReindexSummary) -> list[tuple[str, str]]:
        """Advertencias de los documentos nuevos o actualizados en esta reindexación."""
        changed = {*summary.added, *summary.updated}
        return [
            (info.source, warning)
            for info in self.indexer.sources()
            if info.source in changed and info.status == "indexed"
            for warning in info.warnings
        ]

    # --- Reindexación programada (RF-11) ----------------------------------------------------

    def schedule_daily_reindex(self, application: AnyApplication, at: day_time) -> None:
        """Programa la reindexación incremental diaria a la hora ``at`` (con su zona)."""
        if application.job_queue is None:  # pragma: no cover - el extra job-queue es dependencia
            raise RuntimeError("falta python-telegram-bot[job-queue]")
        application.job_queue.run_daily(self.scheduled_reindex, time=at, name="reindex_diario")
        log.info("reindex_scheduled", time=at.strftime("%H:%M"), timezone=str(at.tzinfo))

    async def scheduled_reindex(self, context: Context) -> None:
        """Reindexación diaria: incremental, una a la vez y con aviso solo si hay novedades.

        CA-11.1 a CA-11.3. Si ya hay una en curso (por ejemplo un ``/reindex``), se omite.
        """
        try:
            summary = await self.indexer.reindex_async()
        except ReindexInProgressError:
            log.info("scheduled_reindex_skipped", reason="ya hay una reindexación en curso")
            return
        except Exception as error:
            log.exception("scheduled_reindex_failed")
            await self._notify_admins(
                context.bot, messages.reindex_failed(str(error) or type(error).__name__)
            )
            return
        news = summary.has_changes or summary.has_errors or bool(summary.quarantined)
        log.info("scheduled_reindex_finished", notified=news and self.notify_admins)
        if news:
            stamp = datetime.now(self.timezone).strftime("%H:%M")
            text = (
                messages.SCHEDULED_REINDEX.format(time=stamp)
                + "\n"
                + messages.reindex_summary(summary, self._new_warnings(summary))
            )
            await self._notify_admins(context.bot, text)

    async def _notify_admins(self, bot: Bot, text: str) -> None:
        if not self.notify_admins:
            return
        for admin in self.access.admins:
            try:
                for part in split_message(text):
                    await bot.send_message(admin, part)
            except TelegramError as error:  # p. ej. el admin nunca le escribió al bot
                log.warning("admin_notify_failed", admin=admin, error=str(error))

    # --- Invitaciones (CA-07.5) -------------------------------------------------------------

    async def _admin_only(self, update: Update, command: str) -> bool:
        """``True`` si quien escribe es administrador; si no, se lo dice."""
        if self.access.is_admin(_user_id(update)):
            return True
        await reply(_message(update), messages.ADMIN_ONLY)
        log.info("message_handled", outcome="command", command=command, denied=True)
        return False

    async def invitation(self, update: Update, context: Context) -> None:
        """``/invitacion``: el enlace para compartir con quien deba usar el bot."""
        if not await self._admin_only(update, "invitacion"):
            return
        if self.invite_code is None:
            await reply(_message(update), messages.INVITE_DISABLED)
        else:
            await reply(
                _message(update), messages.invite_link(context.bot.username, self.invite_code)
            )
        log.info("message_handled", outcome="command", command="invitacion")

    async def invited(self, update: Update, context: Context) -> None:
        """``/invitados``: quién entró con el enlace vigente y cuánto preguntó hoy."""
        if not await self._admin_only(update, "invitados"):
            return
        if self._invite_hash is None:
            await reply(_message(update), messages.INVITE_DISABLED)
        else:
            users = self.storage.invited_users(self._invite_hash, day=self.today())
            await reply(_message(update), messages.invited_list(users))
        log.info("message_handled", outcome="command", command="invitados")

    async def revoke(self, update: Update, context: Context) -> None:
        """``/revocar <id>``: retira la invitación de un usuario."""
        if not await self._admin_only(update, "revocar"):
            return
        message = _message(update)
        args = context.args or []
        if len(args) != 1 or not args[0].isdigit():
            await reply(message, messages.REVOKE_USAGE)
            return
        target = int(args[0])
        if self.storage.revoke(target):
            text = messages.REVOKED
        elif self.access.is_authorized(target):
            text = messages.REVOKE_CONFIGURED
        else:
            text = messages.NOT_INVITED
        await reply(message, text.format(user_id=target))
        log.info("message_handled", outcome="command", command="revocar", target=target)

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
        kind = classify(text)
        if kind is not None:  # saludo, gracias o despedida: sin modelo (CA-06.5)
            first_name = update.effective_user.first_name if update.effective_user else None
            await reply(message, messages.small_talk(kind, first_name))
            log.info("message_handled", outcome="small_talk", kind=kind.value)
            return
        if not await self._within_limits(message, user_id, text):
            return
        started = time.perf_counter()
        async with self._lock(user_id):
            async with self._typing(context.bot, message.chat_id):
                outcome, answer_text, answer = await self._answer(user_id, text)
            markup = self._download_buttons(answer) if outcome == "answered" else None
            await reply(message, answer_text, markup)
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

    async def _within_limits(self, message: Message, user_id: int, text: str) -> bool:
        """Largo máximo y cupo diario; descuenta el cupo si la pregunta sigue (RNF-08)."""
        if self.max_question_chars is not None and len(text) > self.max_question_chars:
            await reply(message, messages.too_long(self.max_question_chars))
            log.info("message_handled", outcome="too_long", chars=len(text))
            return False
        exempt = self.access.is_admin(user_id)  # CA-R8.4: cuentan, pero sin tope
        quota = self.storage.take_question(
            user_id,
            day=self.today(),
            user_limit=None if exempt else self.daily_questions_per_user,
            total_limit=None if exempt else self.daily_questions_total,
        )
        if quota is Quota.OK:
            return True
        if quota is Quota.USER_LIMIT:
            assert self.daily_questions_per_user is not None
            await reply(message, messages.quota_user(self.daily_questions_per_user))
            log.info("message_handled", outcome="quota_user")
        else:
            await reply(message, messages.QUOTA_TOTAL)
            log.warning("message_handled", outcome="quota_total", limit=self.daily_questions_total)
        return False

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
            """Manda una vez el aviso "escribiendo…"; si Telegram lo rechaza, sigue."""
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramError as error:  # no es motivo para fallar la respuesta
                log.debug("typing_failed", error=str(error))

        async def keep_typing() -> None:
            """Repite el aviso cada cierto tiempo, hasta que se cancele la tarea."""
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

    # --- Descarga del documento fuente (RF-14) ----------------------------------------------

    def _download_buttons(self, answer: Answer | None) -> InlineKeyboardMarkup | None:
        """Un botón por documento citado, sin repetir (CA-14.1)."""
        if not self.source_download or answer is None:
            return None
        documents = list(dict.fromkeys(source.source for source in answer.sources))
        if not documents:
            return None
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Descargar {Path(source).name}",
                        callback_data=DOWNLOAD_PREFIX + document_id(source),
                    )
                ]
                for source in documents
            ]
        )

    def _resolve_download(self, doc_id: str) -> Path | None:
        """Archivo del id, solo si está indexado ahora y dentro de la carpeta (CA-14.2)."""
        if self.knowledge_base is None:
            return None
        root = self.knowledge_base.resolve()
        for info in self.indexer.sources():
            if info.status != "indexed" or document_id(info.source) != doc_id:
                continue
            path = (root / info.source).resolve()
            if path.is_relative_to(root) and path.is_file():
                return path
            log.warning("download_outside_knowledge_base", source=info.source)
        return None

    async def download(self, update: Update, context: Context) -> None:
        """Botón "Descargar <documento>": envía el archivo citado."""
        query = update.callback_query
        if query is None or query.data is None:  # pragma: no cover - el patrón lo garantiza
            return
        await query.answer()
        chat = update.effective_chat
        if not self.source_download or chat is None:
            return
        path = self._resolve_download(query.data.removeprefix(DOWNLOAD_PREFIX))
        if path is None:
            await context.bot.send_message(chat.id, messages.DOWNLOAD_GONE)
            log.info("message_handled", outcome="download", found=False)
            return
        try:
            with path.open("rb") as handle:
                await context.bot.send_document(chat.id, handle, filename=path.name)
        except TelegramError as error:  # p. ej. más de 50 MB
            log.warning("download_failed", error=str(error))
            await context.bot.send_message(chat.id, messages.DOWNLOAD_FAILED)
            return
        log.info("message_handled", outcome="download", found=True)

    async def not_text(self, update: Update, context: Context) -> None:
        """Foto, audio, archivo, sticker o texto vacío (CA-R5.3)."""
        await reply(_message(update), messages.ONLY_TEXT)
        log.info("message_handled", outcome="not_text")

    # --- Errores inesperados ----------------------------------------------------------------

    async def on_error(self, update: object, context: Context) -> None:
        """Registra la traza y avisa al usuario sin mostrarla (RNF-05).

        Sin ``update``, el error viene del polling y no de un mensaje: un corte de red se
        registra como advertencia en una línea (python-telegram-bot reintenta solo).
        """
        if update is None:
            if isinstance(context.error, NetworkError):  # incluye TimedOut
                log.warning("telegram_connection_error", error=str(context.error))
            else:
                log.error("background_error", exc_info=context.error)
            return
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
