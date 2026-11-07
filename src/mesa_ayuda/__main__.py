"""Punto de entrada: ``python -m mesa_ayuda [config.yaml]`` (RNF-02).

Orden de arranque:

1. Consola en UTF-8, para que los errores con tildes se lean bien en Windows.
2. Configuración (``config.yaml`` + ``.env``). Si es inválida, el proceso termina con un
   mensaje que nombra el parámetro (CA-R1.3) y código 2.
3. Logs JSON con los secretos redactados (RNF-03, RNF-06) y avisos de arranque.
4. Indexación inicial: el bot no recibe mensajes hasta que el índice está listo.
5. Polling de Telegram hasta Ctrl+C.

Códigos de salida: 0 = detenido normalmente, 1 = no pudo arrancar (modelo de embeddings,
red), 2 = configuración inválida o token rechazado.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from telegram.error import InvalidToken, NetworkError
from telegram.ext import Application

from mesa_ayuda.answering import AnswerService
from mesa_ayuda.bot.handlers import AnyApplication, BotHandlers
from mesa_ayuda.config import ConfigError, Settings, load_settings
from mesa_ayuda.embeddings import Embedder, EmbeddingModelError, LocalEmbedder
from mesa_ayuda.guards import AccessControl, DuplicateFilter, RateLimiter
from mesa_ayuda.indexing.indexer import Indexer
from mesa_ayuda.llm.base import LLMProvider, create_provider
from mesa_ayuda.logging_setup import configure_logging, get_logger
from mesa_ayuda.retrieval import Retriever
from mesa_ayuda.rewriter import QuestionRewriter
from mesa_ayuda.storage import Storage

log = get_logger("mesa_ayuda")

# Mensajes y botones (RF-14); ediciones, reacciones y demás no se descargan.
ALLOWED_UPDATES = ["message", "callback_query"]


@dataclass
class Runtime:
    """Piezas armadas del bot, listas para el polling."""

    settings: Settings
    indexer: Indexer
    storage: Storage
    handlers: BotHandlers
    application: AnyApplication


def build(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    provider: LLMProvider | None = None,
) -> Runtime:
    """Arma el bot y hace la indexación inicial.

    Args:
        settings: Configuración validada.
        embedder: Modelo de embeddings; por defecto el local de ``EMBEDDING_MODEL``.
        provider: Proveedor de LLM; por defecto el del perfil activo.

    Raises:
        EmbeddingModelError: si el modelo de embeddings no se puede cargar.
    """
    embedder = embedder or LocalEmbedder(settings.embedding_model, settings.data_dir / "models")
    indexer = Indexer(
        knowledge_base=settings.knowledge_base_path,
        data_dir=settings.data_dir,
        embedder=embedder,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        injection_check=settings.injection_check,
    )
    indexer.reindex()  # el resumen queda en el log (reindex_finished)

    provider = provider or create_provider(settings)
    answers = AnswerService(
        retriever=Retriever(
            embedder,
            indexer.search,
            top_k=settings.retrieval_top_k,
            threshold=settings.similarity_threshold,
        ),
        provider=provider,
        rewriter=QuestionRewriter(
            provider,
            settings.rewrite_prompt_path.read_text(encoding="utf-8"),
            max_tokens=settings.llm_rewrite_max_tokens,
        ),
        system_prompt=settings.system_prompt_path.read_text(encoding="utf-8"),
        max_tokens=settings.llm_max_tokens,
    )
    storage = Storage(settings.data_dir / "bot.sqlite3", history_length=settings.history_length)
    handlers = BotHandlers(
        access=AccessControl(settings.allowed_user_ids, settings.admin_user_ids),
        limiter=RateLimiter(settings.rate_limit_per_minute),
        storage=storage,
        indexer=indexer,
        answers=answers,
        log_questions=settings.log_questions,
        duplicates=DuplicateFilter(settings.duplicate_window_seconds),
        invite_code=(settings.invite_code.get_secret_value() if settings.invite_code else None),
        timezone=ZoneInfo(settings.reindex_timezone),
        daily_questions_per_user=settings.daily_questions_per_user,
        daily_questions_total=settings.daily_questions_total,
        max_question_chars=settings.max_question_chars,
        notify_admins=settings.reindex_notify_admins,
        knowledge_base=settings.knowledge_base_path,
        source_download=settings.source_download,
    )
    application: AnyApplication = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .concurrent_updates(True)  # una pregunta lenta no frena a los demás (CA-08.4)
        .build()
    )
    handlers.register(application)
    if settings.schedule_time is not None:  # RF-11
        handlers.schedule_daily_reindex(application, settings.schedule_time)
    return Runtime(settings, indexer, storage, handlers, application)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _fail(message: str) -> None:
    print(f"No se pudo arrancar el bot: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Arranca el bot y bloquea hasta que se detiene."""
    _utf8_console()
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else "config.yaml"
    try:
        settings = load_settings(config_path)
    except ConfigError as error:
        _fail(str(error))
        return 2

    configure_logging(settings.log_level, secrets=settings.secret_values())
    for warning in settings.startup_warnings():
        log.warning("startup_warning", message=warning)

    try:
        runtime = build(settings)
    except EmbeddingModelError as error:
        log.error("startup_failed", error=str(error))
        _fail(str(error))
        return 1

    llm = settings.llm
    log.info(
        "bot_starting",
        provider=llm.provider,
        model=llm.model,
        documents=sum(info.status == "indexed" for info in runtime.indexer.sources()),
        authorized=len(settings.authorized_user_ids),
        admins=len(settings.admin_user_ids),
        invitations=settings.invite_code is not None,
    )
    try:
        runtime.application.run_polling(allowed_updates=ALLOWED_UPDATES)
    except InvalidToken:
        log.error("startup_failed", error="token rechazado")
        _fail("Telegram rechazó TELEGRAM_BOT_TOKEN. Revisa el token en .env (@BotFather).")
        return 2
    except NetworkError as error:
        log.error("startup_failed", error=str(error))
        _fail(f"no hay conexión con Telegram ({error}). Revisa la red y vuelve a intentar.")
        return 1
    finally:
        runtime.storage.close()
    log.info("bot_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
