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

### 2026-11-06 — Estructura y configuración (S2: T-01 a T-05)

**Criterio aplicado a lo generado**:
- La herramienta escribió una función auxiliar `redact_mapping` que nadie usaba; la quité
  antes del commit (constitución, principio IV).
- Riesgo detectado al revisar los logs: `httpx`, que usa python-telegram-bot, registra en
  INFO la URL de cada llamada, y esa URL contiene el token del bot. Se resolvió con dos
  barreras: subir esos loggers a WARNING y redactar secretos en todos los logs, incluidos
  los de librerías. Una prueba lo cubre (`test_token_in_third_party_log_url_is_redacted`).
- En Windows, `zoneinfo` no trae la base de zonas horarias: se agregó `tzdata` como
  dependencia para que la validación de `REINDEX_TIMEZONE` funcione en el equipo del
  evaluador.
- Una clave mal escrita en `config.yaml` (`retrieval.topk`) se habría ignorado en silencio
  con la configuración por defecto de pydantic; se decidió rechazarla con un error.
- Pruebas que verifican la coherencia entre código, `config.yaml` y `.env.example`, para
  que agregar un parámetro sin documentarlo haga fallar la suite.

### 2026-11-06 — Revisión independiente contra la rúbrica

Le pedí a la herramienta lanzar un agente evaluador nuevo, sin el contexto de la
conversación, para que revisara el repositorio contra la rúbrica del enunciado y corriera
la verificación por su cuenta. Resultados que acepté y apliqué:

- **Commits**: la regla "1–2 commits por sección" chocaba con el criterio "commits pequeños
  y atómicos". Cambié a un commit por tarea, hecho solo con la sección completa probada
  (enmienda 1.1.0 de la constitución). S0–S2 quedan como están: no reescribo historial.
- **Bug real en la redacción de secretos**: el patrón del token usaba `\b`, que no corta
  entre `bot` y el número en `.../bot123:ABC.../getMe`. Un token no configurado (de otro
  bot) quedaba visible. Mi prueba pasaba porque usaba el token conocido. Corregido, con una
  prueba de regresión que falla con el código anterior.
- **Bug real en la configuración**: el `.env` se buscaba en el directorio actual, mientras
  que las demás rutas se resuelven respecto a `config.yaml`. Corregido, con prueba.

Hallazgos que dejé para su tarea: calibrar el umbral de similitud con datos (T-37/T-38),
el peso de torch en la instalación (T-12), las decisiones y el costo en la documentación
final (T-41/T-42).

### 2026-11-06 — Ingesta y fragmentación (S3: T-06 a T-11)

**Documentos de ejemplo generados con IA** (lo pide el enunciado, §8): los cuatro
documentos de `knowledge_base/` (empresa ficticia Nubeverde S.A.S.) los redactó la
herramienta con datos concretos y verificables (15 días hábiles, tope de $60.000 diarios,
prioridades P1–P4, auxilio de $85.000). El PDF y el DOCX se generan con
`python -m scripts.build_sample_docs`, para que se puedan reproducir. Para el PDF se
escribió un generador mínimo (`scripts/simple_pdf.py`) en lugar de agregar reportlab como
dependencia solo para eso.

**Criterio aplicado a lo generado**:
- Al imprimir los fragmentos reales del PDF aparecieron dos defectos que las pruebas no
  cubrían: (1) el texto de un PDF no trae líneas en blanco, así que el fragmentador pasaba
  directo de párrafo a oración y pegaba las líneas; (2) "3. GASTOS" se tomaba como fin de
  oración. Se agregó el nivel "línea" al corte jerárquico y se excluyó el punto precedido
  de dígito. Revisar la salida real, no solo las pruebas, fue lo que lo detectó.
- Al solapar fragmentos, el texto repetido se unía con un espacio al párrafo siguiente y
  mezclaba temas en una línea ("…equipos. HORARIO El horario…"). Corregido.
- Pruebas de mutación manuales: rompí a propósito el límite de tamaño (51 pruebas fallaron)
  y el separador después del solapamiento (ninguna falló). Escribí la prueba que faltaba.
- Las pruebas de tamaño y solapamiento usan textos aleatorios con semilla fija (200+ casos)
  en lugar de ejemplos escritos a mano.
