# Uso de inteligencia artificial

> **Estado: bitácora en progreso.** Este archivo se llena durante el desarrollo, no al
> final (constitución, flujo de desarrollo). La versión final (T-44) organizará estas notas
> en las secciones que pide el enunciado: herramientas, flujo de trabajo, prompts
> representativos, sugerencias rechazadas o corregidas, verificación manual y proporción
> generada por IA.

## Herramientas

| Herramienta | Modelo / versión | Uso |
|---|---|---|
| Claude Code (CLI, Windows) | Claude Opus 5.5 (`claude-opus-5-5`) | Lectura del enunciado, revisión del diseño, redacción de artefactos de Spec Kit, código y pruebas |
| GitHub Spec Kit (`specify-cli`) | 1.3.3 | Plantillas y flujo constitution → specify → plan → tasks; skills `speckit-*` en `.claude/skills/` |
| Chat de Claude (sesión previa) | — | Notas de diseño iniciales (`prueba-tecnica-bios-diseno.md`, fuera del repositorio) |

## Bitácora

### 2026-11-06 — Especificación (S0–S1)

**Contexto que le di a la herramienta**: el enunciado (`.docx`) y mis notas de diseño
previas; le pedí compararlos, proponer lo que no había tenido en cuenta y armar un plan por
secciones con 1–2 commits por sección después de probar cada una.

**Lo que propuso la IA y acepté** (24 puntos de la revisión; los principales):
- Prefijos `query:`/`passage:` de e5 y distancia coseno en Chroma (sin esto el umbral no
  tiene sentido).
- Marcador `NO_ENCONTRADO` y fragmentos numerados `[n]` para citar solo lo usado, en lugar
  de citar todos los fragmentos recuperados como decían mis notas.
- Casos de documentos del evaluador: PDF escaneado, `.txt` en cp1252, archivos `~$*`.
- Mostrar el ID de Telegram en el mensaje de acceso denegado.
- Cambio de versión simplificado con metadato `doc_key` y filtro `$in`.
- Límite de mensajes en memoria en vez de SQLite.
- Script de evaluación reproducible en lugar de copiar respuestas desde el teléfono.

**Lo que corregí o rechacé**:
- La IA propuso usar plantillas propias "con la estructura de Spec Kit" porque Spec Kit no
  estaba instalado. **Lo rechacé**: le pedí instalar y usar Spec Kit real. Resultado:
  `uv` + `specify-cli` 1.3.3, skills `speckit-*`, y la carpeta de la funcionalidad apuntada
  a `specs/` para respetar las rutas del enunciado.
- La IA advirtió que 1–2 commits por sección podía restar en "commits atómicos" y propuso
  más commits. **Mantuve mi criterio** (commit por sección probada), con la condición de
  que cada commit referencie todas las tareas que cubre.
- Mis notas decían Python 3.12; la IA verificó que el equipo tiene 3.11.9 y se ajustó a
  `>= 3.11`.

**Correcciones que hizo la propia herramienta al aplicar Spec Kit**:
- La plantilla de spec exige que no haya detalles de implementación. El primer borrador
  mencionaba SHA-256, JSON, similitud coseno y `ruff`/`mypy`; se movieron a `plan.md`
  (registrado en `specs/checklists/requirements.md`).
- Spec Kit numera las tareas `T001`; el enunciado exige `T-01`. Se mantuvo `T-01` y se
  conservaron las marcas `[P]` y `[USn]` de Spec Kit.

**Verificación manual**:
- Comprobé que `.env` y `~$*.docx` quedan ignorados por git (`git check-ignore`).
- Revisión cruzada automática spec ↔ tasks: 21 requisitos, todos con al menos una tarea;
  45 tareas, ninguna referencia rota; sin marcadores pendientes.
- Versiones de las dependencias consultadas en PyPI (`pip index versions`), no de memoria.
