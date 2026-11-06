"""Pruebas de la política de tiempo y reintentos y de la fábrica (T-20; RNF-01, RNF-05)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mesa_ayuda.config import load_settings
from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
    RetryingProvider,
    TransientLLMError,
    create_provider,
)
from mesa_ayuda.llm.fake import FakeProvider

MESSAGES = [ChatMessage("user", "hola")]


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def retrying(fake: FakeProvider, *, retries: int = 2, timeout: float = 5.0) -> RetryingProvider:
    return RetryingProvider(
        fake, timeout_seconds=timeout, max_retries=retries, sleep=SleepRecorder()
    )


async def complete(provider: RetryingProvider) -> str:
    result = await provider.complete("sys", MESSAGES, max_tokens=50)
    return result.text


async def test_transient_errors_are_retried_with_exponential_backoff() -> None:
    fake = FakeProvider(replies=[TransientLLMError("529"), TransientLLMError("429"), "listo"])
    sleep = SleepRecorder()
    provider = RetryingProvider(fake, timeout_seconds=5, max_retries=2, base_delay=1.0, sleep=sleep)

    assert await complete(provider) == "listo"
    assert len(fake.calls) == 3
    first, second = sleep.delays
    assert 0.8 <= first <= 1.2 and 1.6 <= second <= 2.4  # 1 s, 2 s (±20 %)


async def test_unavailable_after_exhausting_retries() -> None:
    # CA-R5.1: servicio caído después de reintentos.
    fake = FakeProvider(default=TransientLLMError("503 overloaded"))

    with pytest.raises(LLMUnavailableError, match="503"):
        await complete(retrying(fake, retries=2))

    assert len(fake.calls) == 3


async def test_zero_retries_means_a_single_attempt() -> None:
    fake = FakeProvider(default=TransientLLMError("red"))

    with pytest.raises(LLMUnavailableError):
        await complete(retrying(fake, retries=0))

    assert len(fake.calls) == 1


@pytest.mark.parametrize("error", [LLMAuthError("401"), LLMBadRequestError("404 modelo")])
async def test_auth_and_bad_request_are_not_retried(error: Exception) -> None:
    fake = FakeProvider(replies=[error, "no debería llegar"])

    with pytest.raises(type(error)):
        await complete(retrying(fake))

    assert len(fake.calls) == 1


async def test_timeout_is_total_including_retries() -> None:
    # CA-R5.2: el usuario no espera más de LLM_TIMEOUT_SECONDS aunque haya reintentos.
    fake = FakeProvider(default=TransientLLMError("lento"))
    provider = RetryingProvider(fake, timeout_seconds=0.2, max_retries=5, base_delay=0.15)

    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(LLMTimeoutError, match=r"0\.2 s"):
        await complete(provider)

    assert loop.time() - start < 0.5


async def test_slow_provider_times_out() -> None:
    provider = RetryingProvider(
        FakeProvider(default="tarde", delay=1.0), timeout_seconds=0.05, max_retries=2
    )

    with pytest.raises(LLMTimeoutError):
        await complete(provider)


async def test_fake_records_the_call() -> None:
    fake = FakeProvider(replies=[lambda system, messages: f"{system}:{messages[-1].content}"])

    result = await fake.complete("S", MESSAGES, max_tokens=7, temperature=0.0)

    assert result.text == "S:hola"
    assert fake.calls[0].max_tokens == 7 and fake.calls[0].messages == tuple(MESSAGES)


# --- fábrica -------------------------------------------------------------------------------

DOTENV = """TELEGRAM_BOT_TOKEN=123:abc
LLM_ANTHROPIC_API_KEY=clave-anthropic
LLM_OPENAI_API_KEY=clave-openai
LLM_GEMINI_API_KEY=clave-gemini
"""
YAML = """
llm:
  provider: {provider}
  timeout_seconds: 12
  max_retries: 3
anthropic:
  model: claude-sonnet-5-5
  effort: medium
openai:
  model: llama-3.3-70b
  base_url: http://localhost:11434/v1
  temperature: 0.2
  reasoning_effort: ""
gemini:
  model: gemini-2.5-pro
  temperature: 0.3
"""


def write_config(tmp_path: Path, provider: str) -> Path:
    (tmp_path / "prompts").mkdir()
    for name in ("system_prompt.md", "rewrite_prompt.md"):
        (tmp_path / "prompts" / name).write_text("prompt", encoding="utf-8")
    (tmp_path / ".env").write_text(DOTENV, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(YAML.format(provider=provider), encoding="utf-8")
    return config


@pytest.mark.parametrize(
    ("provider", "adapter", "expected"),
    [
        (
            "anthropic",
            "AnthropicProvider",
            {"model": "claude-sonnet-5-5", "effort": "medium"},
        ),
        (
            "openai",
            "OpenAIProvider",
            {
                "model": "llama-3.3-70b",
                "base_url": "http://localhost:11434/v1",
                "temperature": 0.2,
                "reasoning_effort": None,
                "token_param": "max_tokens",
            },
        ),
        ("gemini", "GeminiProvider", {"model": "gemini-2.5-pro", "temperature": 0.3}),
    ],
)
def test_factory_builds_the_active_profile(
    tmp_path: Path, provider: str, adapter: str, expected: dict[str, object]
) -> None:
    # CA-R1.4, CA-R1.6: mismo .env y mismo YAML; solo cambia llm.provider.
    settings = load_settings(write_config(tmp_path, provider), env_file=".env")

    built = create_provider(settings)

    assert isinstance(built, RetryingProvider)
    assert built.name == provider
    assert (built.timeout_seconds, built.max_retries) == (12, 3)
    inner = built._inner
    assert type(inner).__name__ == adapter
    assert {name: getattr(inner, name) for name in expected} == expected
