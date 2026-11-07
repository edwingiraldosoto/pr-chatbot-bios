"""Manejadores de Telegram: filtro común, comandos y preguntas (RF-03, RF-06–RF-08, RNF-05).

El orden de evaluación es el de ``specs/contracts/bot-commands.md``:

1. :meth:`BotHandlers.gate` corre antes que todo (grupo -1). Ignora lo que no sea un
   mensaje nuevo en un chat privado, niega el acceso a quien no está autorizado y aplica
   el límite por minuto. Si rechaza, lanza ``ApplicationHandlerStop`` y ningún otro
   manejador corre.
2. En el grupo 0, el primer manejador que coincide: comandos conocidos, comando
   desconocido, texto (pregunta) u otro tipo de mensaje.

Los manejadores no conocen la configuración: reciben sus dependencias ya armadas, así las
pruebas los ejercitan con un Telegram falso y sin red.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from telegram import Message, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from mesa_ayuda.bot import messages
from mesa_ayuda.bot.messages import split_message
from mesa_ayuda.guards import AccessControl, RateDecision, RateLimiter, is_private_chat
from mesa_ayuda.indexing.indexer import ReindexSummary, SourceInfo
from mesa_ayuda.logging_setup import bind_request, get_logger, new_request_id
from mesa_ayuda.storage import Storage

log = get_logger(__name__)

Context = ContextTypes.DEFAULT_TYPE
AnyApplication = Application[Any, Any, Any, Any, Any, Any]


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
        indexer: Índice de documentos (``/fuentes``).
    """

    def __init__(
        self,
        *,
        access: AccessControl,
        limiter: RateLimiter,
        storage: Storage,
        indexer: DocumentIndex,
    ) -> None:
        self.access = access
        self.limiter = limiter
        self.storage = storage
        self.indexer = indexer
        self._user_locks: dict[int, asyncio.Lock] = {}

    def register(self, application: AnyApplication) -> None:
        """Agrega los manejadores a la aplicación, en el orden del contrato."""
        application.add_handler(TypeHandler(Update, self.gate), group=-1)
        new_message = filters.UpdateType.MESSAGE
        application.add_handler(CommandHandler("start", self.start, filters=new_message))
        application.add_handler(CommandHandler("help", self.help, filters=new_message))
        application.add_handler(CommandHandler("reset", self.reset, filters=new_message))
        application.add_handler(CommandHandler("fuentes", self.sources, filters=new_message))
        application.add_handler(MessageHandler(new_message & filters.COMMAND, self.unknown))

    def _lock(self, user_id: int) -> asyncio.Lock:
        """Candado por usuario: sus mensajes se atienden en orden, uno a la vez."""
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    # --- Filtro común (grupo -1) ------------------------------------------------------------

    async def gate(self, update: Update, context: Context) -> None:
        """Chat privado → acceso → límite por minuto (CA-07.2, CA-07.4, CA-R5.4)."""
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

    async def unknown(self, update: Update, context: Context) -> None:
        """Comando que no existe."""
        await reply(_message(update), messages.UNKNOWN_COMMAND)
        log.info("message_handled", outcome="command", command="unknown")
