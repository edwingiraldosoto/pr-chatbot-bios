"""Pruebas del arranque del bot (T-32; RNF-02, CA-R1.3, CA-07.3).

Cada prueba corre en una carpeta temporal (``monkeypatch.chdir``) para no leer nunca el
``.env`` real del equipo. El modelo de embeddings y el LLM son falsos; el polling se
reemplaza para no conectarse a Telegram.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from telegram.error import InvalidToken
from telegram.ext import Application

import mesa_ayuda.__main__ as entry
from mesa_ayuda.config import load_settings
from mesa_ayuda.embeddings import EmbeddingModelError, FakeEmbedder
from mesa_ayuda.llm.fake import FakeProvider
from mesa_ayuda.logging_setup import clear_request
from tests.telegram_fake import TelegramHarness

TOKEN = "987654321:AAFakeTokenForTestsOnly_abcdefghijklmn"
ADMIN = 1572098300


@pytest.fixture
def workdir(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Proyecto mínimo en una carpeta temporal, con un documento y un administrador."""
    monkeypatch.chdir(config_dir)
    (config_dir / "knowledge_base" / "vacaciones.md").write_text(
        "# Días de vacaciones\n\nCada empleado tiene 15 días hábiles de vacaciones al año.",
        encoding="utf-8",
    )
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump({"retrieval": {"similarity_threshold": 0.1}}), encoding="utf-8"
    )
    (config_dir / ".env").write_text(
        f"TELEGRAM_BOT_TOKEN={TOKEN}\nLLM_ANTHROPIC_API_KEY=sk-ant-test-0123456789\n"
        f"ADMIN_USER_IDS={ADMIN}\n",
        encoding="utf-8",
    )
    yield config_dir
    clear_request()
    logging.getLogger().handlers.clear()


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Modelo de embeddings, LLM y polling falsos para ``main()``."""
    recorded: dict[str, Any] = {"provider": FakeProvider(default="15 días hábiles [1]")}
    monkeypatch.setattr(entry, "LocalEmbedder", lambda *args, **kwargs: FakeEmbedder())
    monkeypatch.setattr(entry, "create_provider", lambda settings: recorded["provider"])

    real_build = entry.build

    def build(settings: Any, **kwargs: Any) -> entry.Runtime:
        runtime = real_build(settings, **kwargs)
        recorded["handlers"] = runtime.handlers
        return runtime

    monkeypatch.setattr(entry, "build", build)

    def fake_polling(self: Application[Any, Any, Any, Any, Any, Any], **kwargs: Any) -> None:
        recorded["polling"] = kwargs
        error = recorded.get("polling_error")
        if error is not None:
            raise error

    monkeypatch.setattr(Application, "run_polling", fake_polling)
    return recorded


def events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_missing_token_stops_with_the_variable_name(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # CA-R1.3, escenario 15 del quickstart.
    (workdir / ".env").write_text("LLM_ANTHROPIC_API_KEY=sk-ant-test-0123456789\n", "utf-8")

    assert entry.main([]) == 2

    err = capsys.readouterr().err
    assert err.startswith("No se pudo arrancar el bot:")
    assert "TELEGRAM_BOT_TOKEN" in err


def test_starts_after_the_initial_index_and_polls_only_messages(
    workdir: Path, fakes: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["config.yaml"]) == 0

    assert fakes["polling"] == {"allowed_updates": ["message"]}
    out = capsys.readouterr().out
    names = [event["event"] for event in events(out)]
    assert names.index("reindex_finished") < names.index("bot_starting")
    starting = next(e for e in events(out) if e["event"] == "bot_starting")
    assert (starting["provider"], starting["documents"], starting["admins"]) == ("anthropic", 1, 1)
    assert TOKEN not in out  # RNF-03
    assert fakes["handlers"].duplicates.window_seconds == 5  # valor de config por defecto
    handlers = fakes["handlers"]
    assert (
        handlers.daily_questions_per_user,
        handlers.daily_questions_total,
        handlers.max_question_chars,
        str(handlers.timezone),
    ) == (50, 300, 1000, "America/Bogota")
    assert (workdir / "data" / "bot.sqlite3").is_file()


def test_empty_access_lists_are_warned_at_startup(
    workdir: Path, fakes: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    # CA-07.3
    (workdir / ".env").write_text(
        f"TELEGRAM_BOT_TOKEN={TOKEN}\nLLM_ANTHROPIC_API_KEY=sk-ant-test-0123456789\n", "utf-8"
    )

    assert entry.main([]) == 0

    warnings = [e for e in events(capsys.readouterr().out) if e["event"] == "startup_warning"]
    assert any("nadie podrá usar el bot" in e["message"] for e in warnings)


def test_rejected_token_exits_with_a_clear_message(
    workdir: Path, fakes: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    fakes["polling_error"] = InvalidToken("Not Found")

    assert entry.main([]) == 2

    assert "Telegram rechazó TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_embedding_model_failure_exits_with_1(
    workdir: Path,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Broken(FakeEmbedder):
        def embed_documents(self, texts: Any) -> list[list[float]]:
            raise EmbeddingModelError("No se pudo descargar el modelo; revisa la conexión")

    monkeypatch.setattr(entry, "LocalEmbedder", lambda *args, **kwargs: Broken())

    assert entry.main([]) == 1

    assert "No se pudo descargar el modelo" in capsys.readouterr().err
    assert "polling" not in fakes


async def test_built_bot_answers_from_the_indexed_documents(workdir: Path) -> None:
    # De punta a punta con las piezas reales: índice, recuperación, prompt, citas e historial.
    provider = FakeProvider(default="Tienes 15 días hábiles al año [1].")
    settings = load_settings("config.yaml")
    runtime = entry.build(settings, embedder=FakeEmbedder(), provider=provider)

    async with TelegramHarness(runtime.handlers) as bot:
        replies = await bot.send(ADMIN, "¿Cuántos días de vacaciones tengo?")
        sources = await bot.send(ADMIN, "/fuentes")
    runtime.storage.close()

    assert replies == [
        'Tienes 15 días hábiles al año.\n\nFuentes: vacaciones.md, sección "Días de vacaciones".'
    ]
    assert sources == ["Documentos que puedo consultar (1):\n1. vacaciones.md (1 fragmento)"]
    assert "<documentos>" in provider.calls[-1].messages[-1].content
