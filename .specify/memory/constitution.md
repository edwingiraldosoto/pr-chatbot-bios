# Constitución — Asistente de mesa de ayuda RAG en Telegram

## Core Principles

### I. Especificación antes que código (NO NEGOCIABLE)

- Ninguna funcionalidad se implementa sin un requisito en `specs/spec.md`, un diseño en
  `specs/plan.md` y una tarea en `specs/tasks.md`.
- El historial de git MUST mostrar los artefactos de `specs/` antes que el código que los
  implementa.
- Si una decisión cambia durante el desarrollo, se actualizan primero los artefactos
  afectados y se registra el cambio con su motivo en el registro de cambios del spec o
  del plan.

Razón: el enunciado evalúa la trazabilidad spec → plan → tareas → commits → código e
invalida la entrega si `specs/` se creó después del código.

### II. Respuestas fundamentadas o ninguna

- El asistente MUST responder solo con información de los fragmentos recuperados de
  `knowledge_base/` y MUST indicar la fuente.
- Ante la duda, el asistente MUST decir que no encontró la información. Una respuesta
  inventada se considera un defecto más grave que una respuesta de "no encontrado".
- Las citas las genera el código a partir de metadatos, no el modelo.

Razón: es la promesa central del producto; una política citada mal es peor que ninguna.

### III. Comportamiento configurable, secretos fuera del repositorio

- Todo parámetro de comportamiento MUST poder cambiarse sin tocar código.
- Los secretos (token de Telegram, API keys) MUST vivir solo en `.env` o variables de
  entorno; nunca en código, configuración versionada, logs ni historial de git.
- El prompt de sistema vive en un archivo, no en el código.
- La configuración se valida al arrancar; un valor inválido detiene el proceso con un
  mensaje que nombra el parámetro.

### IV. Menos cosas, bien hechas

- Los requisitos obligatorios se terminan y prueban antes de empezar cualquier deseable;
  los requisitos propios van después de los deseables.
- Se prefiere una implementación propia pequeña y explicable a un framework que oculte
  el prompt final o el flujo de datos, salvo que se justifique lo contrario en
  `docs/DECISIONES.md`.
- Toda complejidad adicional (concurrencia, versionado, capas de seguridad) MUST tener un
  requisito o riesgo concreto que la justifique.

### V. Pruebas de la lógica central, sin red

- La lógica central (configuración, ingesta, fragmentación, indexación incremental,
  recuperación con umbral, construcción del prompt, control de acceso, límite de mensajes,
  historial) MUST tener pruebas automatizadas.
- Las pruebas MUST correr sin red ni API keys, usando dobles (por ejemplo, un proveedor de
  LLM falso).
- Una sección del plan solo se da por terminada, y solo se hace commit, cuando `pytest`,
  `ruff` y `mypy` pasan.
- La calidad de las respuestas se mide con un conjunto de evaluación reproducible, no con
  "funciona".

### VI. Observabilidad sin secretos

- Cada mensaje procesado MUST dejar un evento de log estructurado (JSON) con ID de
  solicitud, usuario, resultado, fragmentos usados, latencia y tokens.
- Los logs MUST NOT contener secretos; el texto de las preguntas solo se registra en nivel
  de detalle alto o si la configuración lo habilita.
- Todo error que afecte al usuario MUST producir un mensaje comprensible para él, nunca un
  silencio o una traza.

### VII. El asistente solo produce texto

- El asistente MUST NOT tener herramientas, acceso a archivos arbitrarios, red ni sistemas
  externos más allá de la llamada al modelo de lenguaje.
- El contenido de documentos y mensajes de usuario se trata como datos delimitados, nunca
  como instrucciones.
- Las respuestas se envían como texto plano y accesible.

Razón: limita el daño de cualquier prompt injection a "decir algo incorrecto", nunca a
"hacer algo".

## Restricciones técnicas

- Lenguaje: Python >= 3.11, compatible con Windows, macOS y Linux.
- Índice vectorial y embeddings MUST funcionar localmente, sin servicios en la nube; el
  único servicio pago permitido es la API del modelo de lenguaje.
- El proveedor de LLM se elige por configuración mediante una capa de abstracción.
- Ejecución con máximo 4 comandos después de configurar `.env`, documentada en el README.
- Idioma del producto, la documentación y los commits: español.
- Identificadores de requisitos: se conservan los del enunciado (`RF-xx`) y se agregan
  `RNF-xx`, `CA-xx.y` y `T-xx` para mantener la trazabilidad con la prueba.

## Flujo de desarrollo

- Orden: constitución → spec → plan → tareas → implementación por secciones → evaluación →
  documentación → verificación.
- Commits en formato Conventional Commits, en español. Cada commit de implementación
  referencia las tareas que atiende (`[T-07]` o `[T-07, T-08]`).
- Commits atómicos: uno por tarea (o por corrección), hechos solo después de que la sección
  completa del plan pase sus pruebas. Así cada commit deja el repositorio en verde y el
  historial muestra la evolución tarea por tarea.
- La autoría de los commits es del autor del proyecto; el uso de IA no se oculta, se
  documenta en `docs/USO_IA.md`. No se hace `git push` sin solicitud explícita del autor.
- Cada uso relevante de IA (prompt, sugerencia aceptada o rechazada, verificación) se anota
  en `docs/USO_IA.md` durante el desarrollo, no al final.

## Governance

- Esta constitución prevalece sobre cualquier otra práctica del proyecto. Cuando un
  artefacto la contradiga, se corrige el artefacto o se enmienda la constitución.
- Enmiendas: se documentan en este archivo con nueva versión y fecha, y se explican en el
  mensaje del commit.
- Versionado semántico: MAJOR para eliminar o redefinir principios, MINOR para agregar
  principios o secciones, PATCH para aclaraciones.
- Revisión de cumplimiento: antes de la entrega se verifica cada principio contra el
  repositorio (pruebas, escaneo de secretos, trazabilidad de tareas y commits).

**Version**: 1.1.0 | **Ratified**: 2026-11-06 | **Last Amended**: 2026-11-06

### Registro de enmiendas

- **1.1.0 (2026-11-06)**: commits por tarea en lugar de 1–2 por sección. Una revisión
  contra la rúbrica mostró que la regla anterior chocaba con el criterio "commits pequeños
  y atómicos" del enunciado. Se conserva la condición de hacer commit solo con la sección
  probada. Se reformula la regla de autoría para dejar explícito que el uso de IA se
  documenta.
