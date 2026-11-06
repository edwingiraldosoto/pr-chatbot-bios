"""Verifica la configuración del LLM con una llamada real (T-48; CA-R1.6).

Uso, antes de reiniciar el bot con un proveedor, modelo o API key nuevos::

    python -m mesa_ayuda.llm.check                 # usa ./config.yaml y ./.env
    python -m mesa_ayuda.llm.check otra/config.yaml

Muestra qué proveedor, modelo y parámetros quedaron activos y de qué variable salió cada
uno; luego hace una llamada mínima (unos pocos tokens) y dice si funcionó o qué revisar.
Códigos de salida: 0 = funciona, 1 = el proveedor rechazó la llamada, 2 = configuración
inválida.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from mesa_ayuda.config import ConfigError, Settings, load_settings
from mesa_ayuda.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMProvider,
    LLMTimeoutError,
    create_provider,
)

CHECK_SYSTEM = "Responde únicamente con la palabra OK."
CHECK_MESSAGES = [ChatMessage("user", "Prueba de conexión.")]


@dataclass(frozen=True)
class CheckResult:
    """Resultado de la llamada de prueba."""

    ok: bool
    message: str


def describe(settings: Settings) -> list[str]:
    """Líneas con la configuración activa del LLM; la key se muestra solo por su final."""
    llm = settings.llm
    key = llm.api_key.get_secret_value()
    lines = [
        f"Proveedor:   {llm.provider}  (LLM_PROVIDER)",
        f"Modelo:      {llm.model}  ({llm.origins['model']})",
        f"API key:     …{key[-4:]}  ({llm.origins['api_key']})",
    ]
    if llm.base_url:
        lines.append(f"URL:         {llm.base_url}  ({llm.origins['base_url']})")
    temperature = "no se envía" if llm.temperature is None else str(llm.temperature)
    lines.append(f"Temperatura: {temperature}")
    lines.append(f"Esfuerzo:    {llm.effort or 'no se envía'}")
    lines.append(
        f"Límites:     {settings.llm_max_tokens} tokens por respuesta, "
        f"{settings.llm_timeout_seconds:g} s, {settings.llm_max_retries} reintentos"
    )
    return lines


def _hint(error: LLMError, settings: Settings) -> str:
    llm = settings.llm
    if isinstance(error, LLMAuthError):
        return f"Revisa la API key ({llm.origins['api_key']}): no es válida para {llm.provider}."
    if isinstance(error, LLMBadRequestError):
        return (
            f"Revisa el modelo ({llm.origins['model']}) y los parámetros del perfil de "
            f"{llm.provider}: un modelo inexistente o un parámetro que el modelo no acepta "
            "(déjalo vacío para no enviarlo)."
        )
    if isinstance(error, LLMTimeoutError):
        return "El proveedor no respondió a tiempo: revisa la red o sube LLM_TIMEOUT_SECONDS."
    return "El servicio no está disponible: revisa la red o intenta en unos minutos."


async def run_check(settings: Settings, provider: LLMProvider | None = None) -> CheckResult:
    """Hace la llamada de prueba con el proveedor configurado (o el que se pase)."""
    provider = provider or create_provider(settings)
    start = time.perf_counter()
    try:
        result = await provider.complete(
            CHECK_SYSTEM, CHECK_MESSAGES, max_tokens=settings.llm_max_tokens
        )
    except LLMError as error:
        return CheckResult(
            False, f"FALLÓ: {type(error).__name__}: {error}\n{_hint(error, settings)}"
        )
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    if not result.text.strip():
        return CheckResult(
            False,
            "FALLÓ: el modelo respondió vacío. Si razona, sube LLM_MAX_TOKENS o baja el "
            "esfuerzo del perfil.",
        )
    return CheckResult(
        True,
        f"OK: {result.model} respondió en {elapsed_ms} ms "
        f"({result.input_tokens} tokens de entrada, {result.output_tokens} de salida): "
        f"{result.text.strip()[:60]!r}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``python -m mesa_ayuda.llm.check``."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else "config.yaml"
    if isinstance(sys.stdout, io.TextIOWrapper):
        # Las tildes deben verse bien también cuando la salida va a una tubería o a Git Bash.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # El resultado ya se imprime con su explicación; los logs del proveedor lo duplicarían.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    try:
        settings = load_settings(config_path)
    except ConfigError as error:
        print(error)
        return 2
    print("\n".join(describe(settings)))
    result = asyncio.run(run_check(settings))
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
