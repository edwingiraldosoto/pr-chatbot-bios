"""Fixtures compartidas: aíslan las pruebas del entorno real (variables y .env del equipo)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_ayuda.config import Settings, env_name


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Borra cualquier variable de configuración presente en el equipo que corre las pruebas."""
    for field in Settings.model_fields:
        monkeypatch.delenv(env_name(field), raising=False)


@pytest.fixture
def secrets_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Define secretos falsos en el entorno."""
    values = {
        "TELEGRAM_BOT_TOKEN": "987654321:AAFakeTokenForTestsOnly_abcdefghijklmn",
        "LLM_API_KEY": "sk-test-fake-key-0123456789abcdef",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Carpeta temporal con prompts y un config.yaml mínimo; las pruebas lo sobrescriben."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system_prompt.md").write_text("sistema", encoding="utf-8")
    (tmp_path / "prompts" / "rewrite_prompt.md").write_text("reescritura", encoding="utf-8")
    (tmp_path / "knowledge_base").mkdir()
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    return tmp_path
