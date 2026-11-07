"""Textos del bot y formato de las respuestas (RF-06, RF-12, RNF-05).

Todos los textos que ve el usuario viven aquí, para revisarlos en un solo lugar
(``specs/contracts/bot-commands.md``). Son texto plano: el bot no usa Markdown ni HTML, así
que nada se escapa y los lectores de pantalla los leen tal cual (CA-12.1).
"""

from __future__ import annotations

from collections.abc import Sequence

from mesa_ayuda.indexing.indexer import ReindexSummary, SourceInfo

TELEGRAM_LIMIT = 4096  # caracteres por mensaje, medidos en unidades UTF-16

START = (
    "Hola. Soy el asistente de la mesa de ayuda de Grupo Bios.\n\n"
    "Respondo preguntas sobre los documentos internos de la empresa: políticas, "
    "procedimientos y guías. Solo uso esos documentos; si algo no está en ellos, te lo digo.\n\n"
    "Escríbeme tu pregunta como se la harías a un compañero. Por ejemplo: "
    "¿Cuántos días de vacaciones tengo?\n\n"
    "Usa /help para ver cómo preguntar y los comandos disponibles."
)

_HELP_BODY = (
    "Cómo preguntar:\n"
    "Paso 1. Escribe una pregunta concreta, en una sola frase.\n"
    "Paso 2. Si necesitas más detalle, pregunta de nuevo; recuerdo lo último que hablamos.\n"
    "Paso 3. Al final de cada respuesta verás de qué documento salió.\n\n"
    "Ejemplos:\n"
    "- ¿Cuántos días de vacaciones tengo al año?\n"
    "- ¿Cómo solicito el reembolso de un gasto?\n"
    "- ¿Qué hago si olvidé mi contraseña?\n\n"
    "Comandos:\n"
    "/start - Bienvenida.\n"
    "/help - Esta ayuda.\n"
    "/reset - Borra nuestra conversación y empieza de cero.\n"
    "/fuentes - Lista los documentos que puedo consultar."
)
_HELP_ADMIN = (
    "\n\nComandos de administrador:\n"
    "/reindex - Actualiza el índice con los documentos nuevos, modificados o borrados.\n"
    "/reindex full - Reconstruye el índice completo."
)

RESET_DONE = "Listo, borré nuestra conversación."
NO_SOURCES = "No hay documentos indexados."

ACCESS_DENIED = (
    "No tienes acceso a este asistente. Tu ID de Telegram es {user_id}; compártelo con el "
    "administrador si necesitas acceso."
)
RATE_LIMITED = (
    "Has enviado muchos mensajes seguidos. Espera un momento antes de volver a preguntar."
)
ONLY_TEXT = (
    "Solo puedo responder mensajes de texto. Si prefieres hablar, usa el micrófono del "
    "teclado para dictar tu pregunta."
)
NOT_FOUND = (
    "No encontré esa información en los documentos disponibles. Puedes reformular la "
    "pregunta o consultar a la mesa de ayuda."
)
LLM_TIMEOUT = "La respuesta está tardando más de lo normal. Intenta de nuevo en un momento."
LLM_UNAVAILABLE = (
    "El servicio de respuestas no está disponible en este momento. Intenta de nuevo en unos "
    "minutos."
)
UNEXPECTED_ERROR = "Ocurrió un error inesperado al procesar tu mensaje. Intenta de nuevo."
UNKNOWN_COMMAND = "No conozco ese comando. Usa /help para ver los disponibles."

ADMIN_ONLY = "Este comando es solo para administradores."
REINDEX_STARTED = "Reindexación iniciada…"
REINDEX_FULL_STARTED = "Reconstrucción completa del índice iniciada…"
REINDEX_IN_PROGRESS = "Ya hay una reindexación en curso."
REINDEX_USAGE = "Uso: /reindex (cambios) o /reindex full (reconstrucción completa)."
REINDEX_FAILED = "La reindexación falló: {reason}. Revisa el log del bot."


def help_text(*, is_admin: bool) -> str:
    """Ayuda según el rol: los comandos de administrador solo los ven ellos (CA-06.2)."""
    return _HELP_BODY + (_HELP_ADMIN if is_admin else "")


def access_denied(user_id: int) -> str:
    """Mensaje de acceso denegado con el ID del usuario (CA-07.2)."""
    return ACCESS_DENIED.format(user_id=user_id)


def reindex_failed(reason: str) -> str:
    """Aviso al administrador cuando la reindexación no pudo terminar."""
    return REINDEX_FAILED.format(reason=reason.rstrip("."))


# --- /fuentes ------------------------------------------------------------------------------


def sources_list(sources: Sequence[SourceInfo]) -> str:
    """Documentos indexados con su número de fragmentos (CA-06.4).

    Los ilegibles y en cuarentena no se listan: no se pueden citar.
    """
    indexed = [info for info in sources if info.status == "indexed"]
    if not indexed:
        return NO_SOURCES
    lines = [f"Documentos que puedo consultar ({len(indexed)}):"]
    lines += [
        f"{n}. {info.source} ({_count(info.chunks, 'fragmento', 'fragmentos')})"
        for n, info in enumerate(indexed, start=1)
    ]
    return "\n".join(lines)


# --- /reindex ------------------------------------------------------------------------------


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " s"


def reindex_summary(summary: ReindexSummary, warnings: Sequence[tuple[str, str]] = ()) -> str:
    """Resumen para el administrador (CA-08.1, CA-R5.5).

    Args:
        summary: Resultado de la reindexación.
        warnings: ``(documento, advertencia)`` de los documentos nuevos o actualizados.
    """
    if summary.full_rebuild:
        head = (
            f"Reconstrucción completa terminada en {_seconds(summary.duration_s)} "
            f"(motivo: {summary.rebuild_reason}): "
            f"{_count(len(summary.added), 'documento indexado', 'documentos indexados')}."
        )
    else:
        head = (
            f"Reindexación terminada en {_seconds(summary.duration_s)}: "
            f"{_count(len(summary.added), 'nuevo', 'nuevos')}, "
            f"{_count(len(summary.updated), 'actualizado', 'actualizados')}, "
            f"{_count(len(summary.removed), 'eliminado', 'eliminados')}, "
            f"{summary.unchanged} sin cambios."
        )
    lines = [head]
    if summary.unreadable:
        items = "; ".join(f"{source} ({reason})" for source, reason in summary.unreadable)
        lines.append(f"Ilegibles: {items}.")
    if summary.ignored:
        lines.append(
            f"Ignorados: {_count(summary.ignored, 'archivo', 'archivos')} ocultos, temporales "
            "o de formato no soportado."
        )
    if warnings:
        items = "; ".join(f"{source}: {text}" for source, text in warnings)
        lines.append(f"Advertencias: {items}.")
    return "\n".join(lines)


# --- Respuestas largas (CA-R5.6) -----------------------------------------------------------


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _fit(text: str, limit: int) -> int:
    """Cuántos caracteres del inicio de ``text`` caben en ``limit`` unidades UTF-16."""
    if _utf16_len(text) <= limit:
        return len(text)
    units = 0
    for index, char in enumerate(text):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > limit:
            return index
    return len(text)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Divide un texto en partes que Telegram acepta, cortando donde menos estorba.

    Prefiere cortar en un salto de párrafo, luego de línea y luego en un espacio; solo si
    no hay ninguno corta en seco. Telegram mide el límite en unidades UTF-16, así que un
    emoji cuenta doble. Como la línea de fuentes va al final del texto, queda en la última
    parte.
    """
    if limit < 1:
        raise ValueError("limit debe ser al menos 1")
    parts: list[str] = []
    rest = text.strip()
    while rest:
        size = max(_fit(rest, limit), 1)  # al menos un carácter, para avanzar siempre
        if size == len(rest):
            parts.append(rest)
            break
        window = rest[: size + 1]  # el separador puede quedar justo en el límite
        for separator in ("\n\n", "\n", " "):
            cut = window.rfind(separator)
            if 0 < cut <= size:
                part, rest = rest[:cut], rest[cut + len(separator) :]
                break
        else:
            part, rest = rest[:size], rest[size:]
        if part.strip():
            parts.append(part.rstrip())
        rest = rest.lstrip()
    return parts
