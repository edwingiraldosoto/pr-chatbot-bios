"""Pruebas de la reindexación diaria programada (T-35; RF-11)."""

from __future__ import annotations

import asyncio
from datetime import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import mesa_ayuda.__main__ as entry
from mesa_ayuda.bot import messages
from mesa_ayuda.bot.handlers import BotHandlers
from mesa_ayuda.config import load_settings
from mesa_ayuda.embeddings import FakeEmbedder
from mesa_ayuda.guards import AccessControl, RateLimiter
from mesa_ayuda.indexing.indexer import ReindexSummary
from mesa_ayuda.llm.fake import FakeProvider
from mesa_ayuda.storage import Storage
from tests.telegram_fake import TelegramHarness
from tests.unit.answers_fake import ScriptedAnswers
from tests.unit.test_handlers import FakeIndex

ADMINS = [999, 888]


def make(tmp_path: Path, index: FakeIndex, *, notify: bool = True) -> BotHandlers:
    return BotHandlers(
        access=AccessControl(allowed=[111], admins=ADMINS),
        limiter=RateLimiter(10),
        storage=Storage(tmp_path / "bot.sqlite3", history_length=6),
        indexer=index,
        answers=ScriptedAnswers(),
        timezone=ZoneInfo("America/Bogota"),
        notify_admins=notify,
    )


async def run_job(bot: TelegramHarness, handlers: BotHandlers) -> None:
    await handlers.scheduled_reindex(SimpleNamespace(bot=bot.app.bot))  # type: ignore[arg-type]


async def test_changes_are_reported_to_every_admin(tmp_path: Path) -> None:
    # CA-11.1, CA-11.3
    index = FakeIndex()
    index.summary = ReindexSummary(added=["nuevo.md"], unchanged=3, duration_s=0.4)
    handlers = make(tmp_path, index)
    async with TelegramHarness(handlers) as bot:
        await run_job(bot, handlers)

    assert index.calls == [False]  # incremental, no reconstrucción
    for admin in ADMINS:
        (text,) = bot.sent(admin)
        assert text.startswith("Reindexación programada de las ")
        assert "1 nuevo, 0 actualizados, 0 eliminados, 3 sin cambios." in text
    assert bot.sent(111) == []  # los usuarios no administradores no reciben nada


@pytest.mark.parametrize(
    "summary",
    [
        ReindexSummary(unreadable=[("escaneado.pdf", "no tiene texto extraíble")]),
        ReindexSummary(quarantined=[("x.md", "texto sospechoso")]),
    ],
)
async def test_errors_and_quarantine_are_reported_too(
    tmp_path: Path, summary: ReindexSummary
) -> None:
    index = FakeIndex()
    index.summary = summary
    handlers = make(tmp_path, index)
    async with TelegramHarness(handlers) as bot:
        await run_job(bot, handlers)

    assert len(bot.sent(ADMINS[0])) == 1


async def test_nothing_new_means_no_message(tmp_path: Path) -> None:
    # CA-11.3: solo si hubo cambios o errores.
    index = FakeIndex()
    index.summary = ReindexSummary(unchanged=4)
    handlers = make(tmp_path, index)
    async with TelegramHarness(handlers) as bot:
        await run_job(bot, handlers)

    assert bot.sent() == []


async def test_notify_admins_off_stays_silent(tmp_path: Path) -> None:
    index = FakeIndex()
    index.summary = ReindexSummary(added=["a.md"])
    handlers = make(tmp_path, index, notify=False)
    async with TelegramHarness(handlers) as bot:
        await run_job(bot, handlers)

    assert bot.sent() == [] and index.calls == [False]


async def test_skips_when_another_reindex_is_running(tmp_path: Path) -> None:
    # CA-11.2: nunca dos a la vez; la programada se omite sin avisar.
    index = FakeIndex()
    index.release = asyncio.Event()
    handlers = make(tmp_path, index)
    async with TelegramHarness(handlers) as bot:
        manual = asyncio.create_task(bot.send(ADMINS[0], "/reindex"))
        while not index.is_reindexing:
            await asyncio.sleep(0)
        await run_job(bot, handlers)
        index.release.set()
        await manual

    assert index.calls == [False]  # solo la manual
    assert all("programada" not in text for text in bot.sent())


async def test_failure_is_reported_to_admins(tmp_path: Path) -> None:
    index = FakeIndex()
    index.summary = RuntimeError("disco lleno")
    handlers = make(tmp_path, index)
    async with TelegramHarness(handlers) as bot:
        await run_job(bot, handlers)

    assert bot.sent(ADMINS[0]) == [messages.reindex_failed("disco lleno")]


async def test_job_is_scheduled_daily_at_the_configured_time(tmp_path: Path) -> None:
    handlers = make(tmp_path, FakeIndex())
    at = time(2, 30, tzinfo=ZoneInfo("America/Bogota"))
    async with TelegramHarness(handlers) as bot:
        handlers.schedule_daily_reindex(bot.app, at)
        assert bot.app.job_queue is not None
        (job,) = bot.app.job_queue.get_jobs_by_name("reindex_diario")
        trigger: Any = job.job.trigger  # APScheduler CronTrigger

    assert str(trigger.timezone) == "America/Bogota"
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("2", "30")


@pytest.mark.parametrize(("schedule", "jobs"), [("03:15", 1), ("", 0)])
def test_main_build_schedules_only_when_configured(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, schedule: str, jobs: int
) -> None:
    # CA-11.1: vacío = desactivada.
    monkeypatch.chdir(config_dir)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", "sk-ant-test-0123456789")
    monkeypatch.setenv("REINDEX_SCHEDULE_TIME", schedule)
    settings = load_settings("config.yaml", env_file=None)

    runtime = entry.build(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    runtime.storage.close()

    assert runtime.application.job_queue is not None
    assert len(runtime.application.job_queue.get_jobs_by_name("reindex_diario")) == jobs
