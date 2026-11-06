"""Pruebas de los perfiles por proveedor de LLM (T-46; RNF-01, CA-R1.6).

Lo que se protege: cambiar de proveedor es cambiar un solo valor (``LLM_PROVIDER``), cada
proveedor conserva su modelo, su key y sus parámetros, y un error de configuración nombra
la variable que hay que corregir.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mesa_ayuda.config import ConfigError, load_settings

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"
KEYS = {
    "LLM_ANTHROPIC_API_KEY": "sk-ant-test-key-0123456789",
    "LLM_OPENAI_API_KEY": "sk-openai-test-key-0123456789",
    "LLM_GEMINI_API_KEY": "AIza-gemini-test-key-0123456789",
}


@pytest.fixture
def all_keys(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Token del bot y las tres API keys, como en un .env que ya tiene todo."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "987654321:AAFakeTokenForTestsOnly_abcdefghijklmn")
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    return KEYS


def write_yaml(config_dir: Path, data: dict[str, object]) -> Path:
    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("provider", "model", "key", "temperature", "effort"),
    [
        ("anthropic", "claude-haiku-5-5", "LLM_ANTHROPIC_API_KEY", None, "low"),
        ("openai", "gpt-5-mini", "LLM_OPENAI_API_KEY", None, "low"),
        ("gemini", "gemini-2.5-flash", "LLM_GEMINI_API_KEY", 0.1, None),
    ],
)
def test_switching_provider_is_one_value(
    all_keys: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
    key: str,
    temperature: float | None,
    effort: str | None,
) -> None:
    # CA-R1.6: con las tres keys en .env, solo cambia LLM_PROVIDER.
    monkeypatch.setenv("LLM_PROVIDER", provider)

    llm = load_settings(REPO_CONFIG, env_file=None).llm

    assert (llm.provider, llm.model, llm.temperature, llm.effort) == (
        provider,
        model,
        temperature,
        effort,
    )
    assert llm.api_key.get_secret_value() == all_keys[key]
    assert llm.origins["api_key"] == key
    assert llm.origins["model"].startswith(f"LLM_{provider.upper()}_MODEL")


def test_only_the_active_provider_needs_a_key(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_OPENAI_API_KEY", KEYS["LLM_OPENAI_API_KEY"])

    assert load_settings(config_dir / "config.yaml", env_file=None).llm.provider == "openai"


def test_missing_key_names_the_provider_variable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", KEYS["LLM_ANTHROPIC_API_KEY"])
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    with pytest.raises(ConfigError, match=r"API key de gemini.*LLM_GEMINI_API_KEY.*LLM_API_KEY"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_generic_key_is_a_fallback_and_own_key_wins(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LLM_API_KEY", "generica")
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    assert load_settings(config_dir / "config.yaml", env_file=None).llm.origins["api_key"] == (
        "LLM_API_KEY"
    )

    monkeypatch.setenv("LLM_OPENAI_API_KEY", "propia")
    llm = load_settings(config_dir / "config.yaml", env_file=None).llm
    assert llm.api_key.get_secret_value() == "propia"


def test_profile_values_come_from_yaml(all_keys: dict[str, str], config_dir: Path) -> None:
    path = write_yaml(
        config_dir,
        {
            "llm": {"provider": "openai"},
            "openai": {
                "model": "llama-3.3-70b",
                "base_url": "https://api.groq.com/openai/v1",
                "temperature": 0.2,
                "reasoning_effort": "",
                "token_param": "max_tokens",
            },
        },
    )

    llm = load_settings(path, env_file=None).llm

    assert (llm.model, llm.base_url, llm.temperature, llm.effort, llm.token_param) == (
        "llama-3.3-70b",
        "https://api.groq.com/openai/v1",
        0.2,
        None,  # vacío = no se envía
        "max_tokens",
    )


def test_none_from_env_disables_a_parameter(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # En una variable de entorno no se puede escribir "vacío" (se ignora): se usa "none".
    monkeypatch.setenv("LLM_ANTHROPIC_EFFORT", "none")
    monkeypatch.setenv("LLM_ANTHROPIC_MODEL", "claude-haiku-4-5")

    llm = load_settings(config_dir / "config.yaml", env_file=None).llm

    assert (llm.model, llm.effort) == ("claude-haiku-4-5", None)


def test_generic_model_overrides_the_active_profile(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.3")

    llm = load_settings(config_dir / "config.yaml", env_file=None).llm

    assert (llm.model, llm.temperature) == ("gemini-2.5-pro", 0.3)
    assert (llm.origins["model"], llm.origins["temperature"]) == ("LLM_MODEL", "LLM_TEMPERATURE")


def test_forgotten_generic_model_is_warned_after_switching(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El riesgo de las genéricas: LLM_MODEL de Claude sigue ahí al pasar a gemini.
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    warnings = load_settings(config_dir / "config.yaml", env_file=None).startup_warnings()

    assert any("no parece de gemini" in w and "LLM_MODEL" in w for w in warnings)


def test_anthropic_never_gets_a_temperature(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_TEMPERATURE", "0.5")

    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert settings.llm.temperature is None
    assert any("LLM_TEMPERATURE se ignora" in w for w in settings.startup_warnings())


def test_base_url_only_applies_to_openai(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")

    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert settings.llm.base_url is None
    assert any("LLM_BASE_URL se ignora" in w for w in settings.startup_warnings())


def test_api_key_in_a_yaml_profile_is_rejected(all_keys: dict[str, str], config_dir: Path) -> None:
    path = write_yaml(config_dir, {"gemini": {"api_key": "AIza-filtrada"}})

    with pytest.raises(ConfigError, match=r"gemini\.api_key es un secreto.*LLM_GEMINI_API_KEY"):
        load_settings(path, env_file=None)


def test_invalid_effort_names_the_variable(
    all_keys: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "altisimo")

    with pytest.raises(ConfigError, match="LLM_OPENAI_REASONING_EFFORT"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_every_key_present_is_redacted_in_logs(all_keys: dict[str, str], config_dir: Path) -> None:
    secrets = load_settings(config_dir / "config.yaml", env_file=None).secret_values()

    assert set(all_keys.values()) <= set(secrets)
