"""Carga y validación de la configuración del asistente.

Contrato completo en ``specs/contracts/config.md``. Resumen:

* Los **secretos** (``TELEGRAM_BOT_TOKEN`` y las API keys de los proveedores de LLM) solo
  se leen del entorno o de ``.env``; nunca de ``config.yaml``, que está versionado.
* Cada proveedor de LLM tiene su **perfil** (modelo, API key y parámetros propios), así que
  cambiar de proveedor es cambiar ``LLM_PROVIDER`` y reiniciar (CA-R1.6). Ver
  :attr:`Settings.llm`.
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
from dataclasses import dataclass
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
    "llm_rewrite_max_tokens": ("llm", "rewrite_max_tokens"),
    "llm_truncated_retry_max_tokens": ("llm", "truncated_retry_max_tokens"),
    "llm_timeout_seconds": ("llm", "timeout_seconds"),
    "llm_max_retries": ("llm", "max_retries"),
    "llm_anthropic_model": ("anthropic", "model"),
    "llm_anthropic_effort": ("anthropic", "effort"),
    "llm_anthropic_thinking": ("anthropic", "thinking"),
    "llm_openai_model": ("openai", "model"),
    "llm_openai_base_url": ("openai", "base_url"),
    "llm_openai_temperature": ("openai", "temperature"),
    "llm_openai_reasoning_effort": ("openai", "reasoning_effort"),
    "llm_openai_token_param": ("openai", "token_param"),
    "llm_gemini_model": ("gemini", "model"),
    "llm_gemini_temperature": ("gemini", "temperature"),
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
    "duplicate_window_seconds": ("access", "duplicate_window_seconds"),
    "daily_questions_per_user": ("access", "daily_questions_per_user"),
    "daily_questions_total": ("access", "daily_questions_total"),
    "max_question_chars": ("access", "max_question_chars"),
    "log_level": ("logging", "level"),
    "log_questions": ("logging", "log_questions"),
    "data_dir": ("storage", "data_dir"),
    "reindex_schedule_time": ("reindex", "schedule_time"),
    "reindex_timezone": ("reindex", "timezone"),
    "reindex_notify_admins": ("reindex", "notify_admins"),
    "injection_check": ("ingest", "injection_check"),
    "source_download": ("bot", "source_download"),
    "company_name": ("bot", "company_name"),
}

#: Campos que nunca se aceptan desde ``config.yaml`` porque ese archivo se versiona.
SECRET_FIELDS = (
    "telegram_bot_token",
    "llm_api_key",
    "llm_anthropic_api_key",
    "llm_openai_api_key",
    "llm_gemini_api_key",
    "invite_code",
)

LLMProviderName = Literal["anthropic", "openai", "gemini"]

#: Prefijo esperado del ID de modelo de cada proveedor; si no coincide se advierte al
#: arrancar (un ``LLM_MODEL`` olvidado al cambiar de proveedor). OpenAI no se revisa porque
#: con una URL propia sirve modelos de cualquier familia.
MODEL_PREFIXES: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude",),
    "gemini": ("gemini", "gemma"),
}

#: Campos opcionales: un valor vacío (o ``none``) significa "sin valor" (no se envía).
OPTIONAL_FIELDS = (
    *SECRET_FIELDS[1:],
    "llm_model",
    "llm_base_url",
    "llm_temperature",
    "llm_anthropic_effort",
    "llm_anthropic_thinking",
    "llm_openai_base_url",
    "llm_openai_temperature",
    "llm_openai_reasoning_effort",
    "llm_gemini_temperature",
    "reindex_schedule_time",
)

#: Campos de tipo ruta que se resuelven respecto a la carpeta de ``config.yaml``, para que
#: el bot funcione igual sin importar desde qué directorio se ejecute.
PATH_FIELDS = ("knowledge_base_path", "system_prompt_path", "rewrite_prompt_path", "data_dir")

_SCHEDULE_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_INVITE_RE = re.compile(r"[A-Za-z0-9_-]{12,64}")

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


@dataclass(frozen=True)
class LLMSettings:
    """Configuración resuelta del proveedor de LLM activo.

    Attributes:
        provider: Proveedor elegido en ``LLM_PROVIDER``.
        model: ID del modelo.
        api_key: API key del proveedor.
        base_url: URL de una API compatible (solo ``openai``), o ``None``.
        temperature: Temperatura, o ``None`` para no enviarla.
        effort: Esfuerzo de razonamiento, o ``None`` para no enviarlo.
        token_param: Nombre del límite de salida en OpenAI (``auto`` lo decide).
        origins: De qué parámetro salió cada valor, para el registro de arranque.
    """

    provider: LLMProviderName
    model: str
    api_key: SecretStr
    base_url: str | None
    temperature: float | None
    effort: str | None
    token_param: str
    origins: dict[str, str]


class _YamlSource(PydanticBaseSettingsSource):
    """Fuente de pydantic-settings que entrega los valores ya aplanados de ``config.yaml``."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Busca un parámetro en ``config.yaml``. Devuelve ``None`` si no está escrito allí."""
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
    llm_anthropic_api_key: SecretStr | None = None
    llm_openai_api_key: SecretStr | None = None
    llm_gemini_api_key: SecretStr | None = None
    #: Genérica del enunciado: API key del proveedor activo si no tiene la suya.
    llm_api_key: SecretStr | None = None
    #: Código del enlace de invitación ``t.me/<bot>?start=<código>`` (CA-07.5).
    invite_code: SecretStr | None = None

    # --- Modelo de lenguaje: comunes a todos los proveedores ---
    llm_provider: LLMProviderName = "anthropic"
    llm_max_tokens: int = Field(600, ge=50, le=4000)
    llm_rewrite_max_tokens: int = Field(100, ge=20, le=1000)
    #: Límite del único reintento de una respuesta cortada (CA-R5.8); 0 = sin reintento.
    llm_truncated_retry_max_tokens: int = Field(1200, ge=0, le=8000)
    llm_timeout_seconds: float = Field(30.0, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=5)

    # --- Perfiles por proveedor (CA-R1.6); None = el parámetro no se envía ---
    llm_anthropic_model: str = "claude-haiku-5-5"
    llm_anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "low"
    llm_anthropic_thinking: Literal["disabled", "adaptive"] | None = "disabled"
    llm_openai_model: str = "gpt-5-mini"
    llm_openai_base_url: str | None = None
    llm_openai_temperature: float | None = Field(None, ge=0.0, le=2.0)
    llm_openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = "low"
    llm_openai_token_param: Literal["auto", "max_tokens", "max_completion_tokens"] = "auto"
    llm_gemini_model: str = "gemini-2.5-flash"
    llm_gemini_temperature: float | None = Field(0.1, ge=0.0, le=2.0)

    # --- Genéricas del enunciado: si se definen, reemplazan al perfil del proveedor activo ---
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float | None = Field(None, ge=0.0, le=2.0)

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
    duplicate_window_seconds: float = Field(5.0, ge=0.0, le=60.0)
    daily_questions_per_user: int = Field(50, ge=1, le=10_000)
    daily_questions_total: int = Field(300, ge=1, le=100_000)
    max_question_chars: int = Field(1000, ge=50, le=4000)

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
    #: Empresa que nombra la bienvenida de ``/start``; vacío = saludo sin nombre (CA-06.1).
    company_name: str = Field("NubeVerde", max_length=60)

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

    @field_validator(*OPTIONAL_FIELDS, mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Vacío (o ``none`` desde el entorno, donde no se puede escribir vacío) = sin valor."""
        if isinstance(value, str) and value.strip().lower() in ("", "none", "null"):
            return None
        return value

    @field_validator("company_name", mode="before")
    @classmethod
    def _normalize_company_name(cls, value: Any) -> Any:
        """Une los espacios repetidos; ``none`` desde el entorno deja el saludo sin nombre."""
        if not isinstance(value, str):
            return value
        name = " ".join(value.split())
        return "" if name.lower() in ("none", "null") else name

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

    @field_validator("telegram_bot_token")
    @classmethod
    def _secret_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("no puede estar vacío")
        return value

    @field_validator("invite_code")
    @classmethod
    def _check_invite_code(cls, value: SecretStr | None) -> SecretStr | None:
        # Telegram solo acepta A-Z, a-z, 0-9, _ y - en el parámetro ?start= (hasta 64).
        if value is not None and not _INVITE_RE.fullmatch(value.get_secret_value()):
            raise ValueError(
                "debe tener de 12 a 64 caracteres, solo letras sin tilde, números, _ y - "
                "(ejemplo: nubeverde-evaluacion-7Kq2xZ)"
            )
        return value

    @model_validator(mode="after")
    def _check_llm_key(self) -> Settings:
        # Solo la key del proveedor activo es obligatoria; las demás pueden esperar en .env.
        if self._provider_key() is None:
            provider = self.llm_provider
            raise ValueError(
                f"Falta la API key de {provider}: define LLM_{provider.upper()}_API_KEY "
                "(o LLM_API_KEY) en .env (ver .env.example)"
            )
        return self

    @model_validator(mode="after")
    def _check_truncated_retry(self) -> Settings:
        # Reintentar con el mismo límite (o menos) se cortaría igual: solo gasta otra llamada.
        retry = self.llm_truncated_retry_max_tokens
        if retry and retry <= self.llm_max_tokens:
            raise ValueError(
                f"LLM_TRUNCATED_RETRY_MAX_TOKENS ({retry}) debe ser 0 o mayor que "
                f"LLM_MAX_TOKENS ({self.llm_max_tokens})"
            )
        return self

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

    def _provider_key(self) -> tuple[SecretStr, str] | None:
        """API key del proveedor activo y el nombre de la variable de la que salió."""
        name = f"llm_{self.llm_provider}_api_key"
        own: SecretStr | None = getattr(self, name)
        if own is not None and own.get_secret_value().strip():
            return own, env_name(name)
        if self.llm_api_key is not None and self.llm_api_key.get_secret_value().strip():
            return self.llm_api_key, "LLM_API_KEY"
        return None

    @property
    def llm(self) -> LLMSettings:
        """Valores del proveedor activo: genérica del enunciado → perfil → por defecto."""
        provider = self.llm_provider
        origins: dict[str, str] = {}

        def pick(generic: str, own: str | None) -> Any:
            """Toma el primer valor que exista, genérico o del perfil, y anota de dónde salió."""
            label = generic.removeprefix("llm_")
            if getattr(self, generic) is not None:
                origins[label] = env_name(generic)
                return getattr(self, generic)
            if own is None:
                return None
            origins[label] = describe_field(own)
            return getattr(self, own)

        key = self._provider_key()
        if key is None:  # pragma: no cover - lo impide _check_llm_key
            raise ConfigError(f"Falta la API key de {provider}")
        origins["api_key"] = key[1]
        model: str = pick("llm_model", f"llm_{provider}_model")
        base_url = pick("llm_base_url", "llm_openai_base_url") if provider == "openai" else None
        # El SDK de Anthropic ya no acepta temperature (R-05): con anthropic nunca se envía.
        temperature = (
            None
            if provider == "anthropic"
            else pick("llm_temperature", f"llm_{provider}_temperature")
        )
        effort: str | None = {
            "anthropic": self.llm_anthropic_effort,
            "openai": self.llm_openai_reasoning_effort,
            "gemini": None,
        }[provider]
        return LLMSettings(
            provider=provider,
            model=model,
            api_key=key[0],
            base_url=base_url,
            temperature=temperature,
            effort=effort,
            token_param=self.llm_openai_token_param,
            origins=origins,
        )

    def secret_values(self) -> list[str]:
        """Valores de los secretos presentes, para que el sistema de logs los redacte."""
        values = [self.telegram_bot_token.get_secret_value()]
        for name in SECRET_FIELDS[1:]:
            secret: SecretStr | None = getattr(self, name)
            if secret is not None and secret.get_secret_value().strip():
                values.append(secret.get_secret_value())
        return values

    def startup_warnings(self) -> list[str]:
        """Situaciones válidas pero sospechosas que se registran al arrancar."""
        warnings: list[str] = []
        if not self.authorized_user_ids:
            warnings.append(
                "ALLOWED_USER_IDS y ADMIN_USER_IDS están vacíos: nadie podrá usar el bot. "
                "Agrega tu ID de Telegram (el bot te lo muestra al escribirle)."
            )
        llm = self.llm
        prefixes = MODEL_PREFIXES.get(llm.provider)
        if prefixes and not llm.model.lower().startswith(prefixes):
            warnings.append(
                f"El modelo {llm.model!r} no parece de {llm.provider} (sale de "
                f"{llm.origins['model']}). Si cambiaste de proveedor, revisa LLM_MODEL."
            )
        if llm.provider == "anthropic" and self.llm_temperature is not None:
            warnings.append("LLM_TEMPERATURE se ignora con anthropic: su API ya no la acepta.")
        if llm.provider != "openai" and self.llm_base_url is not None:
            warnings.append(f"LLM_BASE_URL se ignora con {llm.provider}: solo aplica a openai.")
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
            if str(key).lower() == "api_key":
                raise ConfigError(
                    f"config.yaml: {section}.api_key es un secreto: va en .env como "
                    f"LLM_{str(section).upper()}_API_KEY, nunca en config.yaml."
                )
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
            # Errores de validación entre campos: pydantic antepone "Value error, ".
            message = str(item.get("msg")).removeprefix("Value error, ")
            lines.append(f"- {message.rstrip('.')}.")
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
