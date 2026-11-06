"""Pruebas del comando de verificación del LLM (T-48; CA-R1.6)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mesa_ayuda.config import Settings, load_settings
from mesa_ayuda.llm.base import LLMAuthError, LLMBadRequestError, LLMTimeoutError
from mesa_ayuda.llm.check import describe, main, run_check
from mesa_ayuda.llm.fake import FakeProvider


@pytest.fixture
def settings(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LLM_OPENAI_API_KEY", "sk-openai-test-key-9876")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    return load_settings(config_dir / "config.yaml", env_file=None)


def test_describe_shows_origins_and_hides_the_key(settings: Settings) -> None:
    text = "\n".join(describe(settings))

    assert "Proveedor:   openai" in text
    assert "gpt-5-mini  (LLM_OPENAI_MODEL" in text
    assert "…9876  (LLM_OPENAI_API_KEY)" in text
    assert "sk-openai-test-key" not in text
    assert "Temperatura: no se envía" in text and "Esfuerzo:    low" in text


async def test_successful_call_is_reported(settings: Settings) -> None:
    fake = FakeProvider(replies=["OK"])

    result = await run_check(settings, fake)

    assert result.ok and result.message.startswith("OK: fake-model respondió")
    assert fake.calls[0].max_tokens == settings.llm_max_tokens


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (LLMAuthError("401"), "LLM_OPENAI_API_KEY"),
        (LLMBadRequestError("404 model"), "LLM_OPENAI_MODEL"),
        (LLMTimeoutError("lento"), "LLM_TIMEOUT_SECONDS"),
    ],
)
async def test_failure_names_what_to_review(
    settings: Settings, error: Exception, hint: str
) -> None:
    result = await run_check(settings, FakeProvider(replies=[error]))

    assert not result.ok and result.message.startswith("FALLÓ")
    assert hint in result.message


async def test_empty_answer_suggests_more_tokens(settings: Settings) -> None:
    result = await run_check(settings, FakeProvider(replies=["  "]))

    assert not result.ok and "LLM_MAX_TOKENS" in result.message


def test_invalid_config_exits_with_2(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump({"llm": {"provider": "gemini"}}), encoding="utf-8"
    )

    code = main([str(config_dir / "config.yaml")])

    assert code == 2
    assert "LLM_GEMINI_API_KEY" in capsys.readouterr().out
