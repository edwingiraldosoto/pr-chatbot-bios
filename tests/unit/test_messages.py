"""Pruebas de los textos del bot y de la división de mensajes (T-28; RF-06, RF-12, CA-R5.6)."""

from __future__ import annotations

import re

import pytest

from mesa_ayuda.bot import messages
from mesa_ayuda.bot.messages import (
    TELEGRAM_LIMIT,
    help_text,
    reindex_summary,
    sources_list,
    split_message,
)
from mesa_ayuda.bot.small_talk import SmallTalk
from mesa_ayuda.indexing.indexer import ReindexSummary, SourceInfo


def utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# --- Textos ---------------------------------------------------------------------------------


def test_admin_commands_only_in_the_admin_help() -> None:
    # CA-06.2
    assert "/reindex" not in help_text(is_admin=False)
    assert "/reindex full" in help_text(is_admin=True)
    for command in ("/start", "/help", "/reset", "/fuentes"):
        assert command in help_text(is_admin=False)


def test_access_denied_includes_the_user_id() -> None:
    # CA-07.2
    assert "Tu ID de Telegram es 1572098300;" in messages.access_denied(1572098300)


def test_reindex_failure_does_not_duplicate_the_period() -> None:
    assert messages.reindex_failed("modelo no disponible.") == (
        "La reindexación falló: modelo no disponible. Revisa el log del bot."
    )


def test_user_texts_are_plain_and_accessible() -> None:
    # CA-12.1: sin Markdown, HTML ni emojis que transmitan información.
    templates = [
        value for name, value in vars(messages).items() if name.isupper() and isinstance(value, str)
    ]
    texts = [
        text.format(user_id=1, reason="motivo", hello="Hola", link="https://t.me/bot?start=codigo")
        for text in templates
    ]
    texts += [help_text(is_admin=True)]
    for text in texts:
        assert not re.search(r"[*`<>]|\[.+\]\(", text), text  # INVITE_CODE lleva _
        assert all(ord(char) < 0x2600 or char == "…" for char in text), text


def test_help_numbers_the_steps() -> None:
    # CA-12.2
    assert "Paso 1." in help_text(is_admin=False) and "Paso 2." in help_text(is_admin=False)


# --- /fuentes -------------------------------------------------------------------------------


def info(source: str, status: str = "indexed", chunks: int = 3) -> SourceInfo:
    return SourceInfo(source, status, chunks, "2026-11-06T10:00:00+00:00", ())


def test_sources_list_shows_only_indexed_documents_with_their_chunks() -> None:
    # CA-06.4: sin ilegibles ni en cuarentena.
    text = sources_list(
        [
            info("politica_vacaciones.md", chunks=4),
            info("escaneado.pdf", status="unreadable", chunks=0),
            info("raro.txt", status="quarantined", chunks=0),
            info("soporte_ti.docx", chunks=1),
        ]
    )

    assert text == (
        "Documentos que puedo consultar (2):\n"
        "1. politica_vacaciones.md (4 fragmentos)\n"
        "2. soporte_ti.docx (1 fragmento)"
    )


def test_sources_list_when_nothing_is_indexed() -> None:
    assert sources_list([]) == "No hay documentos indexados."
    assert sources_list([info("x.pdf", status="unreadable")]) == "No hay documentos indexados."


# --- /reindex -------------------------------------------------------------------------------


def test_incremental_summary_matches_the_contract() -> None:
    summary = ReindexSummary(
        added=["nuevo.md"],
        updated=["cambiado.md"],
        unchanged=3,
        unreadable=[("manual_vpn.pdf", "sin texto extraíble")],
        duration_s=4.2,
    )

    assert reindex_summary(
        summary,
        [("politica_x.pdf", "página 3: texto que parece una instrucción al asistente")],
    ) == (
        "Reindexación terminada en 4,2 s: 1 nuevo, 1 actualizado, 0 eliminados, 3 sin cambios.\n"
        "Ilegibles: manual_vpn.pdf (sin texto extraíble).\n"
        "Advertencias: politica_x.pdf: página 3: texto que parece una instrucción al asistente."
    )


def test_summary_uses_plurals_and_reports_ignored_files() -> None:
    summary = ReindexSummary(
        added=["a.md", "b.md"], removed=["c.md"], updated=[], ignored=1, duration_s=0.04
    )

    assert reindex_summary(summary) == (
        "Reindexación terminada en 0,0 s: 2 nuevos, 0 actualizados, 1 eliminado, 0 sin cambios.\n"
        "Ignorados: 1 archivo ocultos, temporales o de formato no soportado."
    )


def test_full_rebuild_summary_names_the_reason() -> None:
    summary = ReindexSummary(
        added=["a.md", "b.pdf"], full_rebuild=True, rebuild_reason="solicitada", duration_s=12.46
    )

    assert reindex_summary(summary) == (
        "Reconstrucción completa terminada en 12,5 s (motivo: solicitada): 2 documentos indexados."
    )


# --- División de mensajes (CA-R5.6) ---------------------------------------------------------


def test_short_text_is_one_part() -> None:
    assert split_message("  Hola.  ") == ["Hola."]
    assert split_message("") == []


def test_long_answer_is_split_at_paragraphs_and_sources_stay_last() -> None:
    paragraphs = [f"Paso {n}." + " texto" * 150 for n in range(1, 11)]
    sources = 'Fuentes: politica_vacaciones.md, sección "Acumulación".'
    text = "\n\n".join([*paragraphs, sources])

    parts = split_message(text)

    assert len(parts) > 1
    assert all(utf16(part) <= TELEGRAM_LIMIT for part in parts)
    assert all(part.startswith("Paso ") for part in parts)  # ningún párrafo partido
    assert parts[-1].endswith(sources)
    assert "\n\n".join(parts) == text  # no se pierde ni se inventa texto


def test_falls_back_to_lines_then_spaces_then_a_hard_cut() -> None:
    assert split_message("uno\ndos tres", limit=8) == ["uno", "dos tres"]
    assert split_message("uno dos tres", limit=8) == ["uno dos", "tres"]
    assert split_message("abcdefghij", limit=4) == ["abcd", "efgh", "ij"]


def test_separator_right_at_the_limit_is_used() -> None:
    assert split_message("abcd efgh", limit=4) == ["abcd", "efgh"]


def test_emojis_count_double_like_in_telegram() -> None:
    text = "😀" * 3000  # 6000 unidades UTF-16

    parts = split_message(text)

    assert [utf16(part) for part in parts] == [4096, 1904]
    assert "".join(parts) == text


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="al menos 1"):
        split_message("x", limit=0)


@pytest.mark.parametrize(
    ("first_name", "start"),
    [("Edwin", "Hola, Edwin."), ("EDWIN", "Hola, Edwin."), (None, "Hola."), ("  ", "Hola.")],
)
def test_greeting_is_cordial_and_points_to_help(first_name: str | None, start: str) -> None:
    # CA-06.5
    text = messages.small_talk(SmallTalk.GREETING, first_name)

    assert text.startswith(start)
    assert "/help" in text and "¿Cuántos días de vacaciones tengo?" in text


def test_thanks_and_farewell_replies() -> None:
    assert messages.small_talk(SmallTalk.THANKS, "Edwin") == messages.THANKS
    assert messages.small_talk(SmallTalk.FAREWELL) == messages.FAREWELL
