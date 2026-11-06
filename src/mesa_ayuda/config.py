"""Carga y validación de la configuración del asistente.

Contrato completo en ``specs/contracts/config.md``. Resumen:

* Los **secretos** (``TELEGRAM_BOT_TOKEN``, ``LLM_API_KEY``) solo se leen del entorno o de
  ``.env``; nunca de ``config.yaml``, que está versionado.
* El resto de parámetros vive en ``config.yaml`` (anidado y comentado) y cualquiera se puede
  sobrescribir con una variable de entorno con el nombre del enunciado
  (``RETRIEVAL_TOP_K=6``).
* Precedencia: entorno → ``.env`` → ``config.yaml`` → valor por defecto.
* Cualquier error (secreto faltante, valor fuera de rango, clave desconocida, archivo de
  prompt inexistente) se convierte en :class:`ConfigError` con un mensaje que nombra el
  parámetro, para que el proceso no arranque con una configuración a medias (CA-R1.3).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

#: Ubicación de cada parámetro dentro de ``config.yaml``. Es la única tabla que relaciona
#: el nombre plano (el del enunciado, en minúsculas) con la ruta anidada del YAML.
YAML_PATHS: dict[str, tuple[str, str]] = {
    "llm_provider": ("llm", "provider"),
    "llm_model": ("llm", "model"),
    "llm_base_url": ("llm", "base_url"),
    "llm_temperature": ("llm", "temperature"),
    "llm_max_tokens": ("llm", "max_tokens"),
    "llm_timeout_seconds": ("llm", "timeout_seconds"),
    "llm_max_retries": ("llm", "max_retries"),
    "embedding_provider": ("embeddings", "provider"),
    "embedding_model": ("embeddings", "model"),
    "knowledge_base_path": ("knowledge_base", "path"),
    "chunk_size": ("chunking", "size"),
    "chunk_overlap": ("chunking", "overlap"),
    "retrieval_top_k": ("retrieval", "top_k"),
    "similarity_threshold": ("retrieval", "similarity_threshold"),
    "system_prompt_path": ("prompts", "system_prompt_path"),
    "rewrite_prompt_path": ("prompts", "rewrite_prompt_path"),
    "history_length": ("history", "length"),
    "allowed_user_ids": ("access", "allowed_user_ids"),
    "admin_user_ids": ("access", "admin_user_ids"),
    "rate_limit_per_minute": ("access", "rate_limit_per_minute"),
    "log_level": ("logging", "level"),
    "log_questions": ("logging", "log_questions"),
    "data_dir": ("storage", "data_dir"),
    "reindex_schedule_time": ("reindex", "schedule_time"),
    "reindex_timezone": ("reindex", "timezone"),
    "reindex_notify_admins": ("reindex", "notify_admins"),
    "injection_check": ("ingest", "injection_check"),
    "source_download": ("bot", "source_download"),
}

#: Campos que nunca se aceptan desde ``config.yaml`` porque ese archivo se versiona.
SECRET_FIELDS = ("telegram_bot_token", "llm_api_key")

#: Campos de tipo ruta que se resuelven respecto a la carpeta de ``config.yaml``, para que
#: el bot funcione igual sin importar desde qué directorio se ejecute.
PATH_FIELDS = ("knowledge_base_path", "system_prompt_path", "rewrite_prompt_path", "data_dir")

_SCHEDULE_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Valores planos leídos de config.yaml para la carga en curso. Se pasan por ContextVar
# porque pydantic-settings construye las fuentes en un classmethod sin argumentos propios.
_yaml_values: ContextVar[dict[str, Any] | None] = ContextVar("_yaml_values", default=None)


class ConfigError(Exception):
    """La configuración es inválida o está incompleta; el mensaje es apto para el operador."""


def env_name(field: str) -> str:
    """Nombre de la variable de entorno de un campo (``chunk_size`` → ``CHUNK_SIZE``)."""
    return field.upper()


def describe_field(field: str) -> str:
    """Nombre legible de un parámetro, con su variable de entorno y su clave en el YAML."""
    if field in YAML_PATHS:
        return f"{env_name(field)} (config.yaml: {'.'.join(YAML_PATHS[field])})"
    return env_name(field)


class _YamlSource(PydanticBaseSettingsSource):
    """Fuente de pydantic-settings que entrega los valores ya aplanados de ``config.yaml``."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        values = _yaml_values.get() or {}
        return values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_yaml_values.get() or {})


UserIds = Annotated[list[int], NoDecode]


class Settings(BaseSettings):
    """Parámetros validados del asistente. Ver ``specs/contracts/config.md``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Una línea "LLM_BASE_URL=" vacía en .env no debe pisar el valor del YAML.
        env_ignore_empty=True,
    )

    # --- Secretos (solo entorno / .env) ---
    telegram_bot_token: SecretStr
    llm_api_key: SecretStr

    # --- Modelo de lenguaje ---
    llm_provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    llm_base_url: str | None = None
    llm_temperature: float = Field(0.1, ge=0.0, le=1.0)
    llm_max_tokens: int = Field(600, ge=50, le=4000)
    llm_timeout_seconds: float = Field(30.0, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=5)

    # --- Embeddings ---
    embedding_provider: Literal["local"] = "local"
    embedding_model: str = "intfloat/multilingual-e5-small"

    # --- Documentos y fragmentación ---
    knowledge_base_path: Path = Path("knowledge_base")
    chunk_size: int = Field(800, ge=200, le=4000)
    chunk_overlap: int = Field(150, ge=0)

    # --- Recuperación ---
    retrieval_top_k: int = Field(5, ge=1, le=20)
    similarity_threshold: float = Field(0.80, ge=0.0, le=1.0)

    # --- Prompts ---
    system_prompt_path: Path = Path("prompts/system_prompt.md")
    rewrite_prompt_path: Path = Path("prompts/rewrite_prompt.md")

    # --- Conversación y acceso ---
    history_length: int = Field(6, ge=0, le=50)
    allowed_user_ids: UserIds = Field(default_factory=list)
    admin_user_ids: UserIds = Field(default_factory=list)
    rate_limit_per_minute: int = Field(10, ge=1)

    # --- Logs y almacenamiento ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_questions: bool = False
    data_dir: Path = Path("data")

    # --- Reindexación programada e ingesta ---
    reindex_schedule_time: str | None = None
    reindex_timezone: str = "America/Bogota"
    reindex_notify_admins: bool = True
    injection_check: Literal["off", "warn", "quarantine"] = "warn"
    source_download: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Define la precedencia: argumentos → entorno → ``.env`` → ``config.yaml``."""
        return (init_settings, env_settings, dotenv_settings, _YamlSource(settings_cls))

    @field_validator("allowed_user_ids", "admin_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, value: Any) -> Any:
        """Acepta listas del YAML o texto separado por comas desde el entorno."""
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("llm_base_url", "reindex_schedule_time", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("reindex_schedule_time")
    @classmethod
    def _check_schedule(cls, value: str | None) -> str | None:
        if value is not None and not _SCHEDULE_RE.match(value):
            raise ValueError("debe tener formato HH:MM de 24 horas (p. ej. 03:00) o estar vacío")
        return value

    @field_validator("reindex_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"zona horaria desconocida: {value!r} (use p. ej. America/Bogota)"
            ) from exc
        return value

    @field_validator("telegram_bot_token", "llm_api_key")
    @classmethod
    def _secret_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("no puede estar vacío")
        return value

    @model_validator(mode="after")
    def _check_overlap(self) -> Settings:
        # El solapamiento igual o mayor al tamaño haría que el fragmentador nunca avance.
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) debe ser menor que "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        return self

    # --- Ayudas de lectura ---

    @property
    def authorized_user_ids(self) -> frozenset[int]:
        """Usuarios con acceso: autorizados más administradores (CA-07.1)."""
        return frozenset(self.allowed_user_ids) | frozenset(self.admin_user_ids)

    @property
    def schedule_time(self) -> time | None:
        """Hora de la reindexación diaria con zona horaria, o ``None`` si está desactivada."""
        if self.reindex_schedule_time is None:
            return None
        hour, minute = (int(part) for part in self.reindex_schedule_time.split(":"))
        return time(hour, minute, tzinfo=ZoneInfo(self.reindex_timezone))

    def secret_values(self) -> list[str]:
        """Valores de los secretos, para que el sistema de logs los redacte."""
        return [self.telegram_bot_token.get_secret_value(), self.llm_api_key.get_secret_value()]

    def startup_warnings(self) -> list[str]:
        """Situaciones válidas pero sospechosas que se registran al arrancar."""
        warnings: list[str] = []
        if not self.authorized_user_ids:
            warnings.append(
                "ALLOWED_USER_IDS y ADMIN_USER_IDS están vacíos: nadie podrá usar el bot. "
                "Agrega tu ID de Telegram (el bot te lo muestra al escribirle)."
            )
        if not self.knowledge_base_path.is_dir():
            warnings.append(f"La carpeta de documentos no existe: {self.knowledge_base_path}")
        elif not any(self.knowledge_base_path.iterdir()):
            warnings.append(f"La carpeta de documentos está vacía: {self.knowledge_base_path}")
        return warnings


def _flatten_yaml(data: Mapping[str, Any]) -> dict[str, Any]:
    """Convierte el YAML anidado en nombres planos; rechaza claves desconocidas y secretos."""
    by_path = {path: field for field, path in YAML_PATHS.items()}
    flat: dict[str, Any] = {}
    for section, content in data.items():
        if not isinstance(content, Mapping):
            if str(section).lower() in SECRET_FIELDS:
                raise ConfigError(
                    f"{env_name(section)} es un secreto: va en .env, nunca en config.yaml."
                )
            raise ConfigError(f"config.yaml: la sección '{section}' debe contener claves anidadas.")
        for key, value in content.items():
            field = by_path.get((str(section), str(key)))
            if field is None:
                # Una clave mal escrita ignorada en silencio es un error difícil de ver.
                raise ConfigError(f"config.yaml: clave desconocida '{section}.{key}'.")
            flat[field] = value
    return flat


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"No se encontró el archivo de configuración: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml no es YAML válido: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConfigError("config.yaml debe ser un diccionario de secciones.")
    return _flatten_yaml(data)


def _format_validation_error(error: ValidationError) -> str:
    lines = ["La configuración no es válida:"]
    for item in error.errors():
        loc = item.get("loc") or ()
        field = str(loc[0]) if loc else ""
        if item.get("type") == "missing":
            lines.append(f"- Falta {env_name(field)}: defínelo en .env (ver .env.example).")
        elif field:
            lines.append(f"- {describe_field(field)}: {item.get('msg')}.")
        else:
            lines.append(f"- {item.get('msg')}.")
    return "\n".join(lines)


@contextmanager
def _yaml_context(values: dict[str, Any]) -> Iterator[None]:
    token = _yaml_values.set(values)
    try:
        yield
    finally:
        _yaml_values.reset(token)


def load_settings(
    config_path: Path | str = "config.yaml",
    env_file: Path | str | None = ".env",
) -> Settings:
    """Carga, valida y normaliza la configuración.

    Args:
        config_path: ruta de ``config.yaml``. Las rutas relativas que contiene se resuelven
            respecto a la carpeta de este archivo.
        env_file: ruta del ``.env``; ``None`` para no leer ninguno (pruebas). Una ruta
            relativa se toma respecto a la carpeta de ``config.yaml``, igual que las demás,
            para que el resultado no dependa del directorio desde el que se lanza el bot.
            Si no existe, se usan solo las variables de entorno.

    Returns:
        La configuración validada, con rutas absolutas.

    Raises:
        ConfigError: si falta un secreto, un valor es inválido o un archivo de prompt no existe.
    """
    config_path = Path(config_path).resolve()
    if env_file is not None and not Path(env_file).is_absolute():
        env_file = config_path.parent / env_file
    yaml_values = _read_yaml(config_path)
    try:
        with _yaml_context(yaml_values):
            settings = Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from None

    base_dir = config_path.parent
    resolved = {
        name: (path if path.is_absolute() else (base_dir / path).resolve())
        for name in PATH_FIELDS
        if isinstance(path := getattr(settings, name), Path)
    }
    settings = settings.model_copy(update=resolved)

    for name in ("system_prompt_path", "rewrite_prompt_path"):
        prompt_path: Path = getattr(settings, name)
        if not prompt_path.is_file():
            raise ConfigError(f"{describe_field(name)}: no existe el archivo {prompt_path}.")
    return settings
