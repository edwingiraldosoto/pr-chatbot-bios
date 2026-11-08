"""Pruebas de la carga de configuración (T-02, T-03; RNF-01).

Se prioriza lo que haría fallar la puesta en marcha del evaluador: secretos, mensajes de
error que nombran el parámetro, precedencia y coherencia entre el código, config.yaml y
.env.example.
"""

from __future__ import annotations

import re
from datetime import time
from pathlib import Path

import pytest
import yaml

from mesa_ayuda.config import (
    SECRET_FIELDS,
    YAML_PATHS,
    ConfigError,
    Settings,
    env_name,
    load_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_yaml(config_dir: Path, data: dict[str, object]) -> Path:
    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# --- Archivos del repositorio -------------------------------------------------------------


def test_repo_config_loads_with_contract_defaults(secrets_env: dict[str, str]) -> None:
    settings = load_settings(REPO_ROOT / "config.yaml", env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.embedding_model == "intfloat/multilingual-e5-small"
    assert (settings.chunk_size, settings.chunk_overlap) == (800, 150)
    assert settings.retrieval_top_k == 5
    assert settings.similarity_threshold == pytest.approx(0.80)
    assert settings.system_prompt_path == REPO_ROOT / "prompts" / "system_prompt.md"
    assert settings.reindex_schedule_time is None
    assert settings.telegram_bot_token.get_secret_value() == secrets_env["TELEGRAM_BOT_TOKEN"]


def test_every_field_has_a_yaml_location_or_is_secret() -> None:
    # Si alguien agrega un parámetro y olvida su lugar en config.yaml, no se podría configurar.
    assert set(YAML_PATHS) | set(SECRET_FIELDS) == set(Settings.model_fields)


def test_repo_config_yaml_lists_every_parameter() -> None:
    data = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    present = {(section, key) for section, values in data.items() for key in values}
    assert present == set(YAML_PATHS.values())


def test_env_example_documents_every_variable() -> None:
    # CA-R2.2: .env.example contiene todas las variables, cada una con su comentario.
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    lines = text.splitlines()
    documented: set[str] = set()
    for index, line in enumerate(lines):
        match = re.match(r"^#?\s?([A-Z][A-Z0-9_]+)=", line)
        if match:
            documented.add(match.group(1))
            assert lines[index - 1].startswith("#"), f"{match.group(1)} no tiene comentario"
    assert documented == {env_name(field) for field in Settings.model_fields}


# --- Secretos -----------------------------------------------------------------------------


def test_missing_token_names_the_variable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key-0123456789abcdef")

    with pytest.raises(ConfigError, match=r"Falta TELEGRAM_BOT_TOKEN.*\.env"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_blank_secret_is_rejected(
    config_dir: Path, secrets_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "   ")

    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_secret_in_config_yaml_is_rejected(config_dir: Path, secrets_env: dict[str, str]) -> None:
    path = write_yaml(config_dir, {"telegram_bot_token": "123:abc"})

    with pytest.raises(ConfigError, match="secreto"):
        load_settings(path, env_file=None)


def test_secrets_are_not_exposed_by_repr(config_dir: Path, secrets_env: dict[str, str]) -> None:
    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert secrets_env["LLM_API_KEY"] not in repr(settings)
    assert set(settings.secret_values()) == set(secrets_env.values())


# --- Precedencia --------------------------------------------------------------------------


def test_env_overrides_yaml(
    config_dir: Path, secrets_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_yaml(config_dir, {"retrieval": {"top_k": 3}})
    monkeypatch.setenv("RETRIEVAL_TOP_K", "7")

    assert load_settings(path, env_file=None).retrieval_top_k == 7


def test_dotenv_is_read_and_real_env_wins(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = config_dir / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=111111:from-dotenv-file-value\n"
        "LLM_API_KEY=key-from-dotenv-file\n"
        "HISTORY_LENGTH=4\n"
        "LLM_BASE_URL=\n",
        encoding="utf-8",
    )
    path = write_yaml(
        config_dir, {"history": {"length": 2}, "llm": {"base_url": "http://local:11434/v1"}}
    )
    monkeypatch.setenv("LLM_API_KEY", "key-from-real-environment")

    settings = load_settings(path, env_file=env_file)

    assert settings.history_length == 4  # .env gana a config.yaml
    assert (
        settings.llm_api_key.get_secret_value() == "key-from-real-environment"
    )  # entorno gana a .env
    assert (
        settings.llm_base_url == "http://local:11434/v1"
    )  # una línea vacía en .env no pisa el YAML


# --- Validaciones -------------------------------------------------------------------------


def test_unknown_yaml_key_is_rejected(config_dir: Path, secrets_env: dict[str, str]) -> None:
    path = write_yaml(config_dir, {"retrieval": {"topk": 3}})

    with pytest.raises(ConfigError, match=r"clave desconocida 'retrieval\.topk'"):
        load_settings(path, env_file=None)


def test_out_of_range_value_names_env_var_and_yaml_key(
    config_dir: Path, secrets_env: dict[str, str]
) -> None:
    path = write_yaml(config_dir, {"retrieval": {"top_k": 0}})

    with pytest.raises(ConfigError, match=r"RETRIEVAL_TOP_K \(config\.yaml: retrieval\.top_k\)"):
        load_settings(path, env_file=None)


def test_overlap_must_be_smaller_than_chunk_size(
    config_dir: Path, secrets_env: dict[str, str]
) -> None:
    path = write_yaml(config_dir, {"chunking": {"size": 400, "overlap": 400}})

    with pytest.raises(ConfigError, match="CHUNK_OVERLAP"):
        load_settings(path, env_file=None)


def test_truncated_retry_limit_defaults_to_twice_the_answer_limit(
    secrets_env: dict[str, str],
) -> None:
    settings = load_settings(REPO_ROOT / "config.yaml", env_file=None)

    assert (settings.llm_max_tokens, settings.llm_truncated_retry_max_tokens) == (600, 1200)


def test_truncated_retry_limit_must_exceed_the_answer_limit(
    config_dir: Path, secrets_env: dict[str, str]
) -> None:
    path = write_yaml(config_dir, {"llm": {"max_tokens": 600, "truncated_retry_max_tokens": 600}})

    with pytest.raises(ConfigError, match="LLM_TRUNCATED_RETRY_MAX_TOKENS"):
        load_settings(path, env_file=None)


def test_truncated_retry_can_be_disabled_with_zero(
    config_dir: Path, secrets_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_TRUNCATED_RETRY_MAX_TOKENS", "0")

    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert settings.llm_truncated_retry_max_tokens == 0


def test_user_ids_accept_csv_and_admins_are_authorized(
    config_dir: Path, secrets_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_USER_IDS", "111, 222")
    monkeypatch.setenv("ADMIN_USER_IDS", "999")

    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert settings.allowed_user_ids == [111, 222]
    assert settings.authorized_user_ids == {111, 222, 999}


def test_non_numeric_user_id_is_rejected(
    config_dir: Path, secrets_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_USER_IDS", "111,@edwin")

    with pytest.raises(ConfigError, match="ALLOWED_USER_IDS"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_relative_paths_resolve_against_config_dir(
    config_dir: Path,
    secrets_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("otro_directorio"))

    settings = load_settings(config_dir / "config.yaml", env_file=None)

    assert settings.knowledge_base_path == (config_dir / "knowledge_base").resolve()
    assert settings.data_dir == (config_dir / "data").resolve()


def test_relative_env_file_is_read_next_to_config_yaml(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # Regresión: el .env se buscaba en el directorio actual y no junto a config.yaml.
    (config_dir / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=111111:from-dotenv-file-value\nLLM_API_KEY=key-from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path_factory.mktemp("otro_directorio"))

    settings = load_settings(config_dir / "config.yaml")

    assert settings.llm_api_key.get_secret_value() == "key-from-dotenv"


def test_missing_prompt_file_is_reported(config_dir: Path, secrets_env: dict[str, str]) -> None:
    path = write_yaml(config_dir, {"prompts": {"system_prompt_path": "./no_existe.md"}})

    with pytest.raises(ConfigError, match=r"SYSTEM_PROMPT_PATH.*no existe"):
        load_settings(path, env_file=None)


@pytest.mark.parametrize("value", ["3:00", "24:00", "03:60", "tres"])
def test_invalid_schedule_time_is_rejected(
    config_dir: Path, secrets_env: dict[str, str], value: str
) -> None:
    path = write_yaml(config_dir, {"reindex": {"schedule_time": value}})

    with pytest.raises(ConfigError, match="REINDEX_SCHEDULE_TIME"):
        load_settings(path, env_file=None)


def test_schedule_time_carries_timezone(config_dir: Path, secrets_env: dict[str, str]) -> None:
    path = write_yaml(
        config_dir, {"reindex": {"schedule_time": "03:30", "timezone": "America/Bogota"}}
    )

    scheduled = load_settings(path, env_file=None).schedule_time

    assert scheduled is not None
    assert (scheduled.hour, scheduled.minute) == (3, 30)
    assert scheduled.tzinfo is not None and str(scheduled.tzinfo) == "America/Bogota"
    assert isinstance(scheduled, time)


def test_unknown_timezone_is_rejected(config_dir: Path, secrets_env: dict[str, str]) -> None:
    path = write_yaml(config_dir, {"reindex": {"timezone": "America/Medellin"}})

    with pytest.raises(ConfigError, match="REINDEX_TIMEZONE"):
        load_settings(path, env_file=None)


def test_startup_warns_when_nobody_has_access(
    config_dir: Path, secrets_env: dict[str, str]
) -> None:
    settings = load_settings(config_dir / "config.yaml", env_file=None)

    warnings = settings.startup_warnings()

    assert any("nadie podrá usar el bot" in warning for warning in warnings)
    assert any("vacía" in warning for warning in warnings)


def test_duplicate_window_default_override_and_range(
    secrets_env: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CA-R5.7: 5 s por defecto, 0 lo desactiva, más de 60 es un error que nombra la variable.
    assert load_settings(config_dir / "config.yaml", env_file=None).duplicate_window_seconds == 5

    monkeypatch.setenv("DUPLICATE_WINDOW_SECONDS", "0")
    assert load_settings(config_dir / "config.yaml", env_file=None).duplicate_window_seconds == 0

    monkeypatch.setenv("DUPLICATE_WINDOW_SECONDS", "120")
    with pytest.raises(ConfigError, match="DUPLICATE_WINDOW_SECONDS"):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_invite_code_is_optional_secret_and_validated(
    secrets_env: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CA-07.5: sin código no hay invitaciones; con código, se redacta en los logs.
    assert load_settings(config_dir / "config.yaml", env_file=None).invite_code is None

    monkeypatch.setenv("INVITE_CODE", "nubeverde-evaluacion-7Kq2xZ")
    settings = load_settings(config_dir / "config.yaml", env_file=None)
    assert settings.invite_code is not None
    assert "nubeverde-evaluacion-7Kq2xZ" in settings.secret_values()

    for bad in ("corto", "con espacio y tilde á", "x" * 65):
        monkeypatch.setenv("INVITE_CODE", bad)
        with pytest.raises(ConfigError, match=r"INVITE_CODE.*12 a 64 caracteres"):
            load_settings(config_dir / "config.yaml", env_file=None)


def test_invite_code_in_yaml_is_rejected(secrets_env: dict[str, str], config_dir: Path) -> None:
    (config_dir / "config.yaml").write_text("invite_code: nubeverde-evaluacion-7Kq2xZ\n", "utf-8")

    with pytest.raises(ConfigError):
        load_settings(config_dir / "config.yaml", env_file=None)


def test_cost_limits_defaults_and_ranges(
    secrets_env: dict[str, str], config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # RNF-08
    settings = load_settings(config_dir / "config.yaml", env_file=None)
    assert (
        settings.daily_questions_per_user,
        settings.daily_questions_total,
        settings.max_question_chars,
    ) == (50, 300, 1000)

    monkeypatch.setenv("DAILY_QUESTIONS_TOTAL", "0")
    with pytest.raises(ConfigError, match="DAILY_QUESTIONS_TOTAL"):
        load_settings(config_dir / "config.yaml", env_file=None)
