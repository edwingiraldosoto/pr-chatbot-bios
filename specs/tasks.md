---
description: "Lista de tareas del asistente de mesa de ayuda RAG en Telegram"
---

# Tareas: Asistente de mesa de ayuda con RAG en Telegram

**Entrada**: documentos de diseño en `specs/` (spec.md, plan.md, research.md,
data-model.md, contracts/, quickstart.md)

**Prerrequisitos**: plan.md, spec.md

**Pruebas**: obligatorias para la lógica central (constitución, principio V; CA-R7.1). Cada
tarea de código incluye sus pruebas en el mismo cambio.

**Organización**: generado con `/speckit-tasks`. Las fases siguen el orden de dependencias
del plan (S2–S10) y cada tarea indica la historia de usuario y los requisitos que atiende.
Cada fase termina con `pytest`, `ruff` y `mypy` en verde y se cierra con 1 o 2 commits que
referencian sus tareas (`feat(index): … [T-14, T-15]`).

## Formato: `T-NN [P?] [Historia] Descripción — archivos — requisitos`

- **[P]**: se puede hacer en paralelo con otras [P] de la misma fase (archivos distintos).
- **[USn]**: historia de usuario del spec. Las tareas de infraestructura no llevan historia.
- Estado: `[ ]` pendiente, `[x]` terminada. El estado final y el commit de cada tarea se
  registran en la [tabla de trazabilidad](#trazabilidad-y-estado).

---

## Fase S2: Configuración del proyecto

**Propósito**: estructura, herramientas de calidad, configuración, logs y prompts.

- [ ] T-01 Estructura `src/mesa_ayuda/`, `pyproject.toml` con dependencias fijadas, configuración de `ruff`, `mypy` y `pytest`, `.gitattributes` (fin de línea LF) — `pyproject.toml`, `src/mesa_ayuda/__init__.py`, `tests/conftest.py` — RNF-07
- [ ] T-02 [US7] Carga y validación de configuración (`.env` + `config.yaml` + entorno, precedencia y reglas de [contracts/config.md](contracts/config.md)) con pruebas: secreto faltante, defaults, overlap ≥ size, IDs inválidos, override por entorno — `src/mesa_ayuda/config.py`, `tests/unit/test_config.py` — RNF-01
- [ ] T-03 [P] [US7] `config.yaml` con todos los parámetros comentados y `.env.example` con todas las variables, valor de ejemplo y comentario — `config.yaml`, `.env.example` — RNF-01, CA-R2.2
- [ ] T-04 [P] Logs estructurados JSON con ID de solicitud y redacción de secretos, con prueba de que el token no aparece — `src/mesa_ayuda/logging_setup.py`, `tests/unit/test_logging.py` — RNF-03, RNF-06
- [ ] T-05 [P] [US1] Prompt de sistema y prompt de reformulación según [contracts/prompt.md](contracts/prompt.md) — `prompts/system_prompt.md`, `prompts/rewrite_prompt.md` — RF-03, RF-04, RF-12, RNF-04

**Punto de control**: `python -m pytest` y la configuración se cargan sin errores.

---

## Fase S3: Base — ingesta y fragmentación (bloquea todas las historias)

**Propósito**: convertir la carpeta en fragmentos con metadatos.

- [ ] T-06 [US6] Descubrimiento de archivos: recursivo, filtros de ocultos, `~$*` y extensiones no soportadas, huella SHA-256 — `src/mesa_ayuda/ingest/discovery.py`, `tests/unit/test_discovery.py` — RF-01 (CA-01.3)
- [ ] T-07 [P] [US1] Lectores `.md` (secciones por encabezado) y `.txt` (UTF-8 → cp1252) — `src/mesa_ayuda/ingest/loaders.py`, `tests/unit/test_loaders.py` — RF-01 (CA-01.1, CA-01.2)
- [ ] T-08 [P] [US1] Lector `.pdf` por página; sin texto extraíble → ilegible — `src/mesa_ayuda/ingest/loaders.py`, `tests/unit/test_loaders.py` — RF-01 (CA-01.4)
- [ ] T-09 [P] [US1] Lector `.docx` con secciones por estilo de título — `src/mesa_ayuda/ingest/loaders.py`, `tests/unit/test_loaders.py` — RF-01 (deseable)
- [ ] T-10 [US1] Fragmentador jerárquico (sección → párrafo → oración → caracteres) con solapamiento y encabezado antepuesto — `src/mesa_ayuda/ingest/chunker.py`, `tests/unit/test_chunker.py` — RF-02 (CA-02.1–CA-02.3)
- [ ] T-11 [P] Documentos de ejemplo de una empresa ficticia (vacaciones `.md`, reembolsos `.pdf`, soporte TI `.docx`, trabajo remoto `.txt`) con datos concretos y verificables; fixtures de prueba (corrupto, cp1252, `~$`) — `knowledge_base/`, `tests/fixtures/` — sección 8 del enunciado

**Punto de control**: los 4 documentos de ejemplo producen fragmentos con fuente, sección y página.

---

## Fase S4: Base — índice incremental (bloquea todas las historias)

**Propósito**: índice persistente, incremental y seguro ante consultas concurrentes.

- [ ] T-12 Interfaz `Embedder`, `LocalE5Embedder` (prefijos `query:`/`passage:`) y `FakeEmbedder`; medir tamaño y tiempo de instalación y registrar si se cambia a `fastembed` — `src/mesa_ayuda/embeddings.py`, `tests/unit/test_embeddings.py` — RF-02
- [ ] T-13 Envoltura de Chroma persistente con distancia coseno y filtro por versiones activas — `src/mesa_ayuda/vectorstore.py`, `tests/integration/test_vectorstore.py` — RF-02 (CA-02.4, CA-02.5)
- [ ] T-14 [P] [US6] Manifest: lectura, escritura atómica y cálculo de diferencias (nuevo, cambiado, sin cambios, eliminado, renombrado) — `src/mesa_ayuda/indexing/manifest.py`, `tests/unit/test_manifest.py` — RF-09 (CA-09.1, CA-09.4)
- [ ] T-15 [US6] Indexador incremental con cambio de versión por documento, retiro de ilegibles y eliminados, `ReindexSummary` — `src/mesa_ayuda/indexing/indexer.py`, `tests/integration/test_indexer.py` — RF-02, RF-09 (CA-09.2, CA-09.3, CA-09.6)
- [ ] T-16 [US6] Reconstrucción completa por cambio de ajustes o manifest/índice faltante o corrupto (colección nueva + cambio de puntero) — `src/mesa_ayuda/indexing/indexer.py`, `tests/integration/test_indexer.py` — RF-09 (CA-09.5)
- [ ] T-17 [US6] Ejecución en hilo y candado no bloqueante; prueba de consulta durante el cambio de versión — `src/mesa_ayuda/indexing/indexer.py`, `tests/integration/test_indexer.py` — RF-08 (CA-08.4, CA-08.5)

**Punto de control**: reiniciar sin cambios no reindexa; actualizar un documento nunca mezcla versiones.

---

## Fase S5: Historias 1 y 2 — respuesta fundamentada y "no encontrado" (P1) 🎯 MVP

**Meta**: dada una pregunta, devolver una respuesta con fuentes o "no encontrado".

**Prueba independiente**: con el LLM falso, una pregunta relevante devuelve texto + fuentes;
una irrelevante devuelve "no encontrado" sin llamar al LLM.

- [ ] T-18 [US1] [US2] Recuperación: top-K, similitud = 1 − distancia, umbral — `src/mesa_ayuda/retrieval.py`, `tests/unit/test_retrieval.py` — RF-03, RF-04 (CA-04.1)
- [ ] T-19 [P] [US1] Construcción del prompt con fragmentos numerados, delimitados y escapados — `src/mesa_ayuda/prompting.py`, `tests/unit/test_prompting.py` — RF-03, RNF-04 (CA-R4.1)
- [ ] T-20 [P] Interfaz `LLMProvider`, errores unificados, reintentos con espera exponencial, fábrica y `FakeProvider` — `src/mesa_ayuda/llm/base.py`, `src/mesa_ayuda/llm/fake.py`, `tests/unit/test_llm_base.py` — RNF-01, RNF-05 (CA-R5.1, CA-R5.2)
- [ ] T-21 [P] Adaptador Anthropic; confirmar ID de modelo y precios vigentes en la documentación oficial — `src/mesa_ayuda/llm/anthropic.py` — CA-R1.4
- [ ] T-22 [P] Adaptador OpenAI con `base_url` para APIs compatibles — `src/mesa_ayuda/llm/openai.py` — CA-R1.4
- [ ] T-23 [P] Adaptador Gemini (opcional; se omite si compromete el tiempo) — `src/mesa_ayuda/llm/gemini.py` — CA-R1.4
- [ ] T-24 [US4] Reformulación de preguntas de seguimiento con respaldo si falla — `src/mesa_ayuda/rewriter.py`, `tests/unit/test_rewriter.py` — RF-05 (CA-05.1)
- [ ] T-25 [US1] [US2] Servicio de respuesta: rechazo previo, `NO_ENCONTRADO`, extracción de `[n]` y citas por código, uso de tokens — `src/mesa_ayuda/answering.py`, `tests/unit/test_answering.py` — RF-03, RF-04 (CA-03.1–CA-03.3, CA-04.2)

**Punto de control**: MVP del motor de respuestas probado sin Telegram.

---

## Fase S6: Historias 3, 4, 5, 6 y 8 — bot de Telegram (P1–P3)

**Meta**: el motor queda disponible en Telegram con acceso, comandos, historial y errores.

**Prueba independiente**: escenarios 1–15 de [quickstart.md](quickstart.md) con el bot real.

- [ ] T-26 [US4] [US8] Almacenamiento SQLite: historial con límite por usuario, `/reset`, preguntas sin respuesta — `src/mesa_ayuda/storage.py`, `tests/unit/test_storage.py` — RF-05 (CA-05.2–CA-05.4), RF-10 (CA-10.1)
- [ ] T-27 [P] [US3] Guardas: control de acceso, solo chats privados, límite por minuto, validación de mensaje — `src/mesa_ayuda/guards.py`, `tests/unit/test_guards.py` — RF-07, CA-R5.3, CA-R5.4
- [ ] T-28 [P] [US10] Textos del bot y división de mensajes largos — `src/mesa_ayuda/bot/messages.py`, `tests/unit/test_messages.py` — RNF-05 (CA-R5.6), RF-12
- [ ] T-29 [US5] Comandos `/start`, `/help` (por rol), `/reset`, `/fuentes` — `src/mesa_ayuda/bot/handlers.py`, `tests/unit/test_handlers.py` — RF-06
- [ ] T-30 [US1] [US2] Manejador de texto (typing, servicio de respuesta, errores) y de mensajes no textuales — `src/mesa_ayuda/bot/handlers.py`, `tests/unit/test_handlers.py` — RF-03, RNF-05
- [ ] T-31 [US6] `/reindex` y `/reindex full` solo para administradores, con resumen — `src/mesa_ayuda/bot/handlers.py`, `tests/unit/test_handlers.py` — RF-08
- [ ] T-32 [US7] Punto de entrada: config → logs → indexación inicial → polling — `src/mesa_ayuda/__main__.py` — RNF-02
- [ ] T-33 [P] [US8] Exportación de preguntas sin respuesta a CSV — `scripts/unanswered.py` — RF-10 (CA-10.2)

**Punto de control**: obligatorios RF-01 a RF-07 y deseables RF-08 a RF-10 funcionando en Telegram.

---

## Fase S7: Historia 9 y extras (P3, solo si S2–S6 están completas)

- [ ] T-34 [US9] Revisión de texto sospechoso (`off`/`warn`/`quarantine`) con fixture inyectado — `src/mesa_ayuda/ingest/injection_check.py`, `tests/unit/test_injection_check.py`, `tests/fixtures/` — RF-13
- [ ] T-35 [US9] Reindexación diaria con JobQueue y aviso a administradores — `src/mesa_ayuda/__main__.py`, `tests/unit/test_scheduler.py` — RF-11
- [ ] T-36 [P] Dockerfile y `compose.yaml` con volúmenes para `data/` y la caché del modelo — `Dockerfile`, `compose.yaml` — CA-R2.4
- [ ] T-37 Botón "Descargar <documento>" por fuente citada (prioridad más baja) — `src/mesa_ayuda/bot/handlers.py` — RF-14

---

## Fase S8: Evaluación y calibración

- [ ] T-38 Conjunto de evaluación (12+ preguntas: 3+ fuera de alcance, 2+ seguimientos, 2 de injection) y script reproducible con aciertos de recuperación — `scripts/evaluate.py`, `tests/eval/questions.yaml` — SC-001–SC-003
- [ ] T-39 Ejecutar la evaluación, calibrar umbral, tamaño de fragmento y prompt; prueba con lector de pantalla; registrar cambios en spec y plan — `docs/EVALUACION.md`, `specs/spec.md`, `specs/plan.md` — RF-03, RF-04, RF-05, RF-12, RNF-04

---

## Fase S9: Documentación

- [ ] T-40 [P] README completo: requisitos, instalación, BotFather, ID de usuario, ejecución en ≤ 4 comandos, documentos, ejemplos, proveedores, limitaciones, mapa — `README.md` — RNF-02
- [ ] T-41 [P] Arquitectura: diagrama, flujo de ingesta, flujo de pregunta con prompt final, estructura de carpetas — `docs/ARQUITECTURA.md`
- [ ] T-42 [P] Registro de decisiones (las 8 exigidas + las del diseño) en formato contexto → opciones → decisión → justificación → consecuencias — `docs/DECISIONES.md`
- [ ] T-43 [P] Criterio técnico: lo más difícil, qué cambiaría, producción, riesgos, costo por consulta con cálculo, exclusiones, medición de valor — `docs/CRITERIO_TECNICO.md`
- [ ] T-44 [P] Uso de IA: herramientas y versiones, flujo, prompts representativos, sugerencias rechazadas, verificación manual, proporción generada — `docs/USO_IA.md`

---

## Fase S10: Verificación final

- [ ] T-45 Clon limpio siguiendo solo el README; escaneo de secretos del historial; revisión de seguridad y de código; estado final de esta lista — todo el repositorio — RNF-02, RNF-03, SC-004, SC-007

---

## Dependencias y orden de ejecución

### Dependencias entre fases

- **S2** no depende de nada. **S3** y **S4** dependen de S2 y bloquean todas las historias.
- **S5** depende de S4. **S6** depende de S5. **S7** depende de S6.
- **S8** depende de S6 (y de S7 para los casos de injection desde documentos).
- **S9** se puede adelantar en paralelo; se cierra después de S8. **S10** es la última.

### Dependencias entre historias

- US1 y US2 (P1) son el MVP del motor (S5); US3 (P1) llega con el bot (S6).
- US4, US5, US6, US7 y US8 dependen del bot (S6). US9 y US10 son incrementos sobre S6.

### Oportunidades de paralelismo

- S2: T-03, T-04 y T-05 en paralelo después de T-02.
- S3: los lectores T-07, T-08 y T-09 y los documentos T-11.
- S5: T-19 a T-23 (archivos distintos).
- S9: los cinco documentos.

## Estrategia de implementación

1. **MVP**: S2 → S3 → S4 → S5 (motor probado con LLM falso) → S6 (bot con obligatorios).
2. **Deseables**: RF-08 a RF-10 quedan dentro de S4 y S6, porque salen casi gratis del manifest.
3. **Extras** (S7): solo con S2–S6 completas; es lo primero que se recorta si falta tiempo.
4. **Evaluación y documentación** (S8–S9) no se recortan: valen casi la mitad de la nota.

## Trazabilidad y estado

Estado al momento de la entrega (se actualiza al cerrar cada fase).

| Tarea | Requisitos | Fase | Estado | Commit |
|---|---|---|---|---|
| T-01 | RNF-07 | S2 | Pendiente | — |
| T-02 | RNF-01 | S2 | Pendiente | — |
| T-03 | RNF-01 | S2 | Pendiente | — |
| T-04 | RNF-03, RNF-06 | S2 | Pendiente | — |
| T-05 | RF-03, RF-04, RF-12, RNF-04 | S2 | Pendiente | — |
| T-06 | RF-01 | S3 | Pendiente | — |
| T-07 | RF-01 | S3 | Pendiente | — |
| T-08 | RF-01 | S3 | Pendiente | — |
| T-09 | RF-01 | S3 | Pendiente | — |
| T-10 | RF-02 | S3 | Pendiente | — |
| T-11 | Enunciado §8 | S3 | Pendiente | — |
| T-12 | RF-02 | S4 | Pendiente | — |
| T-13 | RF-02 | S4 | Pendiente | — |
| T-14 | RF-09 | S4 | Pendiente | — |
| T-15 | RF-02, RF-09 | S4 | Pendiente | — |
| T-16 | RF-09 | S4 | Pendiente | — |
| T-17 | RF-08 | S4 | Pendiente | — |
| T-18 | RF-03, RF-04 | S5 | Pendiente | — |
| T-19 | RF-03, RNF-04 | S5 | Pendiente | — |
| T-20 | RNF-01, RNF-05 | S5 | Pendiente | — |
| T-21 | RNF-01 | S5 | Pendiente | — |
| T-22 | RNF-01 | S5 | Pendiente | — |
| T-23 | RNF-01 | S5 | Pendiente | — |
| T-24 | RF-05 | S5 | Pendiente | — |
| T-25 | RF-03, RF-04 | S5 | Pendiente | — |
| T-26 | RF-05, RF-10 | S6 | Pendiente | — |
| T-27 | RF-07, RNF-05 | S6 | Pendiente | — |
| T-28 | RNF-05, RF-12 | S6 | Pendiente | — |
| T-29 | RF-06 | S6 | Pendiente | — |
| T-30 | RF-03, RNF-05 | S6 | Pendiente | — |
| T-31 | RF-08 | S6 | Pendiente | — |
| T-32 | RNF-02 | S6 | Pendiente | — |
| T-33 | RF-10 | S6 | Pendiente | — |
| T-34 | RF-13 | S7 | Pendiente | — |
| T-35 | RF-11 | S7 | Pendiente | — |
| T-36 | RNF-02 | S7 | Pendiente | — |
| T-37 | RF-14 | S7 | Pendiente | — |
| T-38 | SC-001–SC-003 | S8 | Pendiente | — |
| T-39 | RF-03–RF-05, RF-12, RNF-04 | S8 | Pendiente | — |
| T-40 | RNF-02 | S9 | Pendiente | — |
| T-41 | Enunciado §6.2 | S9 | Pendiente | — |
| T-42 | Enunciado §6.3 | S9 | Pendiente | — |
| T-43 | Enunciado §6.6 | S9 | Pendiente | — |
| T-44 | Enunciado §6.4 | S9 | Pendiente | — |
| T-45 | RNF-02, RNF-03 | S10 | Pendiente | — |

### Cobertura de requisitos

| Requisito | Tareas |
|---|---|
| RF-01 | T-06, T-07, T-08, T-09 |
| RF-02 | T-10, T-12, T-13, T-15 |
| RF-03 | T-05, T-18, T-19, T-25, T-30 |
| RF-04 | T-05, T-18, T-25 |
| RF-05 | T-24, T-26 |
| RF-06 | T-29 |
| RF-07 | T-27 |
| RF-08 | T-17, T-31 |
| RF-09 | T-14, T-15, T-16 |
| RF-10 | T-26, T-33 |
| RF-11 | T-35 |
| RF-12 | T-05, T-28, T-39 |
| RF-13 | T-34 |
| RF-14 | T-37 |
| RNF-01 | T-02, T-03, T-20–T-23 |
| RNF-02 | T-32, T-36, T-40, T-45 |
| RNF-03 | T-04, T-45 |
| RNF-04 | T-05, T-19, T-39 |
| RNF-05 | T-20, T-27, T-28, T-30 |
| RNF-06 | T-04 |
| RNF-07 | T-01 y las pruebas de cada tarea |
