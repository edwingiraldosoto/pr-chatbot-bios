# Especificación de la funcionalidad: Asistente de mesa de ayuda con RAG en Telegram

**Rama de la funcionalidad**: `main` (proyecto de una sola funcionalidad; ver `specs/plan.md`)

**Creada**: 2026-11-06

**Estado**: Aprobada para planeación

**Entrada**: Enunciado "Prueba técnica – Asistente de mesa de ayuda con RAG en Telegram"
(Servicios Grupo Bios S.A.S., 2026-11-06) y notas de diseño del autor.

> Generada con GitHub Spec Kit (`/speckit-specify`, plantilla `spec-template` traducida al
> español). Describe **qué** hace el sistema y **para quién**; el **cómo** está en
> [`plan.md`](plan.md) y el desglose en [`tasks.md`](tasks.md). Los requisitos RF-01 a RF-10
> conservan los identificadores del enunciado; RF-11 a RF-14 son requisitos propios. Los
> cambios posteriores quedan en el [registro de cambios](#registro-de-cambios).

**Prioridades**: **O** obligatorio (enunciado) · **D** deseable (enunciado) · **P** propio
(se implementa solo después de que los obligatorios y deseables estén terminados y probados).

## Escenarios de usuario y pruebas *(obligatorio)*

### Actores

| Actor | Descripción |
|---|---|
| Colaborador | Usuario autorizado que hace preguntas por Telegram. |
| Administrador | Colaborador con permisos extra: reindexar la base de conocimiento y recibir avisos de indexación. Mantiene la carpeta de documentos. |
| Usuario no autorizado | Cualquier cuenta de Telegram que encuentra el bot pero no está en la configuración. |
| Operador / evaluador | Persona que instala y configura el sistema con su propio token de Telegram y su propia API key. |

### Historia de usuario 1 — Preguntar y recibir una respuesta con fuente (Prioridad: P1)

Como colaborador, quiero preguntarle al bot en lenguaje natural sobre una política interna y
recibir una respuesta corta con la fuente, para no tener que buscar en los documentos.

**Por qué esta prioridad**: es el valor central del producto; sin ella no hay asistente.

**Prueba independiente**: con un documento de vacaciones en la carpeta, preguntar
"¿Cuántos días de vacaciones tengo?" y verificar la cifra y la fuente.

**Escenarios de aceptación**:

1. **Dado** un documento indexado que dice "15 días hábiles de vacaciones", **cuando** un colaborador pregunta cuántos días de vacaciones tiene, **entonces** responde 15 días hábiles y cita el documento y la sección.
2. **Dado** un PDF indexado, **cuando** la respuesta sale de él, **entonces** la fuente incluye el número de página.
3. **Dados** cinco fragmentos recuperados de los que el modelo usó dos, **cuando** se envía la respuesta, **entonces** solo se citan los documentos de esos dos fragmentos.

**Requisitos**: RF-01, RF-02, RF-03, RF-12.

---

### Historia de usuario 2 — Saber cuándo el bot no sabe (Prioridad: P1)

Como colaborador, quiero que el bot me diga claramente cuando algo no está en los
documentos, para no actuar con base en información inventada.

**Por qué esta prioridad**: una respuesta inventada sobre una política es peor que ninguna.

**Prueba independiente**: preguntar algo ajeno a la base ("¿Cuál es la capital de
Australia?", "¿Cuál es el salario del gerente?") y verificar la respuesta de "no encontrado".

**Escenarios de aceptación**:

1. **Dado** que ningún fragmento es relevante para la pregunta, **cuando** el colaborador pregunta, **entonces** el bot responde que no encontró la información en los documentos, sin consultar al modelo de lenguaje y sin citar fuentes.
2. **Dados** fragmentos relevantes que no contienen la respuesta, **cuando** el modelo lo determina, **entonces** el bot da la misma respuesta de "no encontrado" sin fuentes.
3. **Dada** cualquier respuesta de "no encontrado", **entonces** la pregunta queda registrada para el administrador.

**Requisitos**: RF-04, RF-10.

---

### Historia de usuario 3 — Solo personas autorizadas (Prioridad: P1)

Como administrador, quiero que solo personas autorizadas usen el bot, para proteger la
información interna.

**Por qué esta prioridad**: el bot es público por su nombre de usuario; sin control de
acceso, cualquiera consulta documentos internos y genera costo.

**Prueba independiente**: escribirle al bot desde una cuenta no autorizada.

**Escenarios de aceptación**:

1. **Dado** un usuario que no está en la lista de autorizados, **cuando** escribe cualquier mensaje o comando, **entonces** recibe "acceso denegado" con su ID numérico de Telegram, y su mensaje no se procesa.
2. **Dado** un administrador que no está en la lista de autorizados, **cuando** escribe, **entonces** es atendido (los administradores están autorizados implícitamente).
3. **Dado** un mensaje en un grupo o canal, **entonces** el bot lo ignora.

**Requisitos**: RF-07.

---

### Historia de usuario 4 — Preguntas de seguimiento (Prioridad: P2)

Como colaborador, quiero hacer preguntas de seguimiento ("¿y se pueden acumular?") sin
repetir el contexto.

**Por qué esta prioridad**: así es como la gente conversa; sin esto, las respuestas de
seguimiento caen en "no encontrado".

**Prueba independiente**: preguntar por vacaciones y luego "¿y se pueden acumular?".

**Escenarios de aceptación**:

1. **Dada** una pregunta previa sobre vacaciones, **cuando** el colaborador pregunta "¿y se pueden acumular?", **entonces** la respuesta trata sobre la acumulación de vacaciones.
2. **Dado** que dos colaboradores conversan a la vez, **entonces** el historial de uno nunca influye en las respuestas al otro.
3. **Dado** que el colaborador envía `/reset`, **cuando** vuelve a preguntar "¿y se pueden acumular?", **entonces** la pregunta se trata sin contexto previo.

**Requisitos**: RF-05, RF-06.

---

### Historia de usuario 5 — Comandos básicos (Prioridad: P2)

Como colaborador, quiero comandos para empezar, pedir ayuda, borrar mi conversación y ver qué
documentos conoce el bot.

**Por qué esta prioridad**: orienta al usuario y le da control sobre su conversación.

**Prueba independiente**: enviar `/start`, `/help`, `/reset` y `/fuentes`.

**Escenarios de aceptación**:

1. **Dado** un usuario autorizado, **cuando** envía `/start`, **entonces** recibe una bienvenida que explica qué hace el bot.
2. **Cuando** envía `/help`, **entonces** recibe instrucciones de uso y los comandos disponibles para su rol.
3. **Cuando** envía `/fuentes`, **entonces** ve la lista de documentos indexados actualmente.

**Requisitos**: RF-06.

---

### Historia de usuario 6 — Mantener la base de conocimiento sin tocar código (Prioridad: P2)

Como administrador, quiero agregar, actualizar o eliminar documentos de la carpeta y que el
bot lo refleje sin reiniciarlo ni modificar código.

**Por qué esta prioridad**: el enunciado exige que agregar o actualizar un documento sea
suficiente; la reindexación sin reinicio es deseable.

**Prueba independiente**: copiar un documento nuevo a la carpeta, enviar `/reindex` y
preguntar por su contenido.

**Escenarios de aceptación**:

1. **Dado** un documento nuevo en la carpeta, **cuando** el administrador envía `/reindex` (o se reinicia el bot), **entonces** el bot responde sobre su contenido y el resumen dice "1 nuevo".
2. **Dado** un documento actualizado, **cuando** se reindexa, **entonces** las respuestas usan solo la versión nueva.
3. **Dado** un documento eliminado de la carpeta, **cuando** se reindexa, **entonces** el bot deja de citarlo.
4. **Dada** una reindexación en curso, **cuando** un colaborador pregunta, **entonces** recibe respuesta con la versión anterior o la nueva completa, nunca una mezcla ni un vacío.
5. **Dado** un colaborador que no es administrador, **cuando** envía `/reindex`, **entonces** recibe "comando solo para administradores".

**Requisitos**: RF-01, RF-02, RF-08, RF-09, RF-11.

---

### Historia de usuario 7 — Instalar y configurar con pocos pasos (Prioridad: P2)

Como operador, quiero instalar y configurar el sistema con pocos comandos y sin tocar código,
usando mi propio token y mi propia API key.

**Por qué esta prioridad**: si el evaluador no logra ejecutarlo siguiendo el README, la
entrega se invalida.

**Prueba independiente**: clonar el repositorio en una máquina limpia y seguir solo el README.

**Escenarios de aceptación**:

1. **Dado** un clon limpio y un `.env` configurado, **cuando** el operador ejecuta los comandos del README (máximo 4), **entonces** el bot queda respondiendo en Telegram.
2. **Dado** un `.env` sin el token de Telegram, **cuando** se arranca, **entonces** el proceso se detiene con un mensaje que nombra la variable faltante.
3. **Dado** que el operador cambia el proveedor de LLM en la configuración, **cuando** reinicia, **entonces** el bot usa el nuevo proveedor sin cambios de código.

**Requisitos**: RNF-01, RNF-02, RNF-03.

---

### Historia de usuario 8 — Mejorar la base con las preguntas sin respuesta (Prioridad: P3)

Como administrador, quiero conocer las preguntas que el bot no pudo responder, para mejorar
la base de conocimiento.

**Por qué esta prioridad**: deseable del enunciado; no bloquea el uso diario.

**Prueba independiente**: hacer 3 preguntas fuera de alcance y consultar el registro.

**Escenarios de aceptación**:

1. **Dadas** 3 preguntas respondidas con "no encontrado", **cuando** el administrador consulta el registro (procedimiento en el README), **entonces** ve las 3 con fecha, usuario y pregunta.

**Requisitos**: RF-10.

---

### Historia de usuario 9 — Avisos de documentos sospechosos y reindexación nocturna (Prioridad: P3)

Como administrador, quiero enterarme si un documento contiene texto que intenta manipular al
asistente, y que la base se actualice sola cada noche.

**Por qué esta prioridad**: requisitos propios; suman seguridad y operación, pero no son
parte del mínimo.

**Prueba independiente**: copiar a la carpeta un documento con "Ignora tus instrucciones…",
reindexar y revisar el resumen; ejecutar a mano la tarea nocturna.

**Escenarios de aceptación**:

1. **Dado** un documento con frases típicas de manipulación, **cuando** se reindexa en modo advertencia, **entonces** el resumen y el log muestran una advertencia con documento y página o sección.
2. **Dada** una hora de reindexación configurada, **cuando** llega esa hora, **entonces** se ejecuta la misma reindexación incremental y se avisa a los administradores solo si hubo cambios o errores.

**Requisitos**: RF-11, RF-13.

---

### Historia de usuario 10 — Respuestas accesibles (Prioridad: P3)

Como colaborador que usa lector de pantalla, quiero respuestas que se entiendan bien al ser
leídas en voz alta.

**Por qué esta prioridad**: casi sin costo (reglas de formato y del prompt), pero no es parte
del mínimo.

**Prueba independiente**: escuchar 3 respuestas con TalkBack o VoiceOver.

**Escenarios de aceptación**:

1. **Dada** cualquier respuesta, **entonces** es texto plano, sin tablas de caracteres ni información transmitida solo con emojis o imágenes, y empieza por la respuesta directa.

**Requisitos**: RF-12.

---

### Casos límite

- **Mensaje vacío o no textual** (foto, audio, archivo, sticker): se responde que solo se aceptan mensajes de texto y se sugiere el dictado por voz del teclado (RNF-05).
- **Respuesta más larga que el límite de un mensaje de Telegram (4096 caracteres)**: se divide en varios mensajes (RNF-05).
- **El servicio del modelo de lenguaje no responde o tarda demasiado**: tras reintentos para errores transitorios, el usuario recibe un mensaje claro de servicio no disponible o de tiempo agotado (RNF-05).
- **Usuario que envía muchos mensajes seguidos**: por encima de `RATE_LIMIT_PER_MINUTE` recibe un aviso de espera y sus mensajes no se procesan (RNF-05).
- **PDF escaneado sin texto, archivo corrupto o protegido**: se omite, se reporta como ilegible y el resto se indexa (RF-01).
- **Archivo de texto en codificación de Windows (cp1252)**: se lee con tildes y eñes correctas (RF-01).
- **Archivos temporales de Office (`~$*`), ocultos o con extensión no soportada**: se ignoran y se registran (RF-01).
- **Carpeta de documentos vacía o inexistente**: el bot arranca, avisa en el log y responde "no encontrado" a todo; `/fuentes` indica que no hay documentos.
- **Documento actualizado cuya nueva versión es ilegible**: se retira del índice y se avisa; no se conserva la versión anterior (RF-09).
- **Cambio de tamaño de fragmento, solapamiento o modelo de embeddings**: se reconstruye todo el índice (RF-09).
- **Dos solicitudes de reindexación simultáneas** (comando y tarea programada): la segunda se rechaza o espera; nunca corren dos a la vez (RF-08, RF-11).
- **Mensaje del usuario que intenta manipular al asistente** ("olvida tus reglas y…"): se trata como pregunta; el asistente no cambia de comportamiento ni revela su configuración (RNF-04).
- **Lista de usuarios autorizados vacía**: nadie tiene acceso y se avisa al arrancar (RF-07).

## Requisitos *(obligatorio)*

### Requisitos funcionales

#### RF-01 — Ingesta de documentos (O; `.docx` D)

El sistema MUST leer los archivos de la carpeta configurada en `KNOWLEDGE_BASE_PATH` (por
defecto `./knowledge_base`), incluidas subcarpetas, en formatos `.md`, `.txt`, `.pdf` y
`.docx`.

- **CA-01.1** Un archivo de cada formato soportado con texto conocido queda indexado y su contenido es recuperable.
- **CA-01.2** Un `.txt` en UTF-8 y otro en Windows-1252 se leen sin caracteres corruptos.
- **CA-01.3** Se ignoran sin error los archivos ocultos, los temporales de Office (`~$*`) y las extensiones no soportadas; cada uno queda en el log.
- **CA-01.4** Un archivo corrupto o sin texto extraíble no detiene la ingesta: se omite, se registra como advertencia y aparece como "ilegible" en el resumen.

#### RF-02 — Indexación persistente (O)

El sistema MUST dividir los documentos en fragmentos, generar embeddings y almacenarlos en un
índice local consultable que persista entre reinicios.

- **CA-02.1** Cada fragmento tiene como máximo `CHUNK_SIZE` caracteres y comparte hasta `CHUNK_OVERLAP` caracteres con el anterior del mismo documento.
- **CA-02.2** Los cortes se hacen preferentemente en límites de sección, luego de párrafo y luego de oración, antes que a mitad de palabra.
- **CA-02.3** Cada fragmento conserva el documento de origen, la sección (si existe) y la página (en PDF).
- **CA-02.4** Al reiniciar sin cambios en la carpeta, el índice no se reconstruye y las consultas funcionan de inmediato.
- **CA-02.5** El índice funciona sin servicios en la nube.

#### RF-03 — Respuesta fundamentada con fuente (O)

El sistema MUST responder solo con base en los fragmentos recuperados e indicar la fuente.

- **CA-03.1** Toda respuesta con contenido termina con las fuentes usadas: nombre del documento y sección o página cuando existan.
- **CA-03.2** Solo se citan los fragmentos que el modelo indicó haber usado, no todos los recuperados.
- **CA-03.3** Las citas se construyen a partir de los metadatos del índice; nunca aparece como fuente un documento que no está indexado.

#### RF-04 — Manejo de lo desconocido (O)

Si la información no está en los documentos, el sistema MUST decirlo claramente y no inventar.

- **CA-04.1** Si ningún fragmento alcanza `SIMILARITY_THRESHOLD`, se responde "no encontrado" **sin consultar al modelo de lenguaje**.
- **CA-04.2** Si hay fragmentos relevantes pero el modelo indica que no contienen la respuesta, se responde lo mismo, sin fuentes.

#### RF-05 — Contexto conversacional (O)

El sistema MUST mantener un historial por usuario para entender preguntas de seguimiento.

- **CA-05.1** Las preguntas de seguimiento del conjunto de evaluación (mínimo 2) se responden sobre el tema de la conversación.
- **CA-05.2** Se conservan como máximo `HISTORY_LENGTH` mensajes por usuario; los más antiguos se descartan.
- **CA-05.3** El historial de un usuario nunca se usa en las respuestas a otro.
- **CA-05.4** El historial persiste entre reinicios.

#### RF-06 — Comandos (O)

- **CA-06.1** `/start` responde con una bienvenida que explica qué hace el bot.
- **CA-06.2** `/help` explica cómo preguntar y lista los comandos del rol del usuario (los de administrador solo se muestran a administradores).
- **CA-06.3** `/reset` borra el historial de quien lo envía y lo confirma.
- **CA-06.4** `/fuentes` lista los documentos indexados actualmente (nombre y número de fragmentos), sin incluir eliminados ni ilegibles.

#### RF-07 — Control de acceso (O)

- **CA-07.1** Solo los IDs en `ALLOWED_USER_IDS` o `ADMIN_USER_IDS` pueden usar el bot.
- **CA-07.2** Los demás reciben un mensaje de acceso denegado con su ID numérico de Telegram; su mensaje no se procesa ni llega al modelo.
- **CA-07.3** Con la lista de autorizados vacía nadie tiene acceso (falla cerrada) y se avisa al arrancar.
- **CA-07.4** Solo se atienden chats privados.

#### RF-08 — Reindexación por comando (D)

- **CA-08.1** `/reindex` procesa documentos nuevos, modificados y eliminados sin reiniciar, y responde con un resumen (nuevos, actualizados, eliminados, sin cambios, ilegibles).
- **CA-08.2** `/reindex full` reconstruye el índice completo.
- **CA-08.3** Un usuario autorizado que no es administrador recibe "comando solo para administradores".
- **CA-08.4** Durante una reindexación el bot sigue respondiendo preguntas y comandos.
- **CA-08.5** Si ya hay una reindexación en curso, una nueva solicitud se rechaza con un aviso.

#### RF-09 — Indexación incremental (D)

- **CA-09.1** Solo se procesan los archivos cuyo **contenido** cambió, no toda la carpeta (cambiar solo la fecha de modificación no provoca reproceso).
- **CA-09.2** Al actualizar un documento, su versión anterior deja de ser recuperable, y ninguna consulta combina ambas versiones ni encuentra el documento vacío.
- **CA-09.3** Al eliminar un documento de la carpeta, sus fragmentos se eliminan y deja de citarse.
- **CA-09.4** Un archivo renombrado se trata como eliminación más archivo nuevo.
- **CA-09.5** Si cambian `CHUNK_SIZE`, `CHUNK_OVERLAP` o el modelo de embeddings, el índice se reconstruye completo automáticamente.
- **CA-09.6** Si la nueva versión de un documento ya indexado es ilegible, el documento se retira del índice y se avisa.

#### RF-10 — Registro de preguntas sin respuesta (D)

- **CA-10.1** Cada respuesta de "no encontrado" guarda fecha, ID de usuario, pregunta original, pregunta reformulada y mejor puntaje de similitud.
- **CA-10.2** El registro persiste entre reinicios y se puede consultar con el procedimiento documentado en el README.

#### RF-11 — Reindexación programada (P)

- **CA-11.1** Con `reindex.schedule_time` configurado, se ejecuta a diario la reindexación incremental de RF-09 a esa hora y en la zona horaria configurada; vacío significa desactivada.
- **CA-11.2** Respeta la regla de una sola reindexación a la vez (CA-08.5).
- **CA-11.3** Con `reindex.notify_admins` activo, los administradores reciben el resumen solo si hubo cambios o errores.

#### RF-12 — Respuestas accesibles (P)

- **CA-12.1** Las respuestas son texto plano, sin tablas de caracteres, y no dependen de emojis ni imágenes para transmitir información.
- **CA-12.2** Empiezan con la respuesta directa, usan oraciones cortas y numeran los pasos ("Paso 1", "Paso 2").

#### RF-13 — Detección de texto sospechoso en documentos (P)

- **CA-13.1** Con `ingest.injection_check: warn`, un documento con frases típicas de manipulación se indexa, pero el log y el resumen muestran una advertencia con documento y página o sección.
- **CA-13.2** Con `quarantine`, ese documento no se indexa.
- **CA-13.3** Con `off`, no se hace la revisión.

#### RF-14 — Descarga del documento fuente (P, prioridad más baja)

- **CA-14.1** Si está habilitado, cada respuesta con fuentes ofrece un botón "Descargar <documento>" por fuente citada.
- **CA-14.2** El botón solo puede enviar documentos indexados dentro de `KNOWLEDGE_BASE_PATH`; su identificador no es una ruta y no permite acceder a otros archivos.

### Requisitos no funcionales

#### RNF-01 — Parametrización

- **CA-R1.1** Todos los parámetros de la sección 4.1 del enunciado existen y cambian el comportamiento sin modificar código.
- **CA-R1.2** Los secretos (`TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`) se leen solo del entorno o de `.env`; el resto vive en un archivo de configuración versionado y puede sobrescribirse con variables de entorno.
- **CA-R1.3** Una configuración inválida o un secreto faltante detienen el arranque con un mensaje que nombra el parámetro y el problema.
- **CA-R1.4** El proveedor de LLM se cambia solo con configuración; se soportan al menos dos proveedores comerciales y cualquier API compatible con el formato de uno de ellos.
- **CA-R1.5** El prompt de sistema se lee del archivo indicado en `SYSTEM_PROMPT_PATH`.
- **CA-R1.6** Cambiar de proveedor de LLM es cambiar `LLM_PROVIDER` y reiniciar el proceso: cada proveedor tiene en la configuración su modelo, su API key y sus parámetros propios (temperatura, esfuerzo de razonamiento, URL), de modo que ningún ajuste de un proveedor o modelo exige modificar ni desplegar código. Un comando verifica la configuración con una llamada real antes de reiniciar el bot.

#### RNF-02 — Ejecución

- **CA-R2.1** Siguiendo solo el README, el proyecto se ejecuta con máximo 4 comandos después de configurar `.env`, en Windows, macOS y Linux.
- **CA-R2.2** `.env.example` contiene todas las variables con valores de ejemplo y un comentario por variable.
- **CA-R2.3** No se necesita ningún servicio pago distinto de la API key del LLM.
- **CA-R2.4** (Deseable) Existe una alternativa de ejecución en contenedores.

#### RNF-03 — Seguridad de credenciales

- **CA-R3.1** Ninguna credencial aparece en el código ni en el historial de git (verificado con un escáner de secretos antes de la entrega).
- **CA-R3.2** Los logs nunca contienen el token de Telegram ni la API key.

#### RNF-04 — Mitigación de prompt injection

- **CA-R4.1** El contenido de los documentos y el mensaje del usuario llegan al modelo delimitados y marcados como datos, no como instrucciones.
- **CA-R4.2** El asistente no tiene herramientas ni acceso a sistemas externos: solo produce texto.
- **CA-R4.3** Las mitigaciones y el riesgo residual están documentados, y al menos 2 casos de injection (uno desde un documento, uno desde un mensaje) se evalúan en `docs/EVALUACION.md`.

#### RNF-05 — Manejo de errores visible

El usuario MUST recibir un mensaje claro, nunca un silencio ni una traza, ante:

- **CA-R5.1** Servicio del modelo caído o con error, después de reintentos para errores transitorios.
- **CA-R5.2** Tiempo de espera agotado (configurable).
- **CA-R5.3** Mensaje vacío o no textual.
- **CA-R5.4** Más de `RATE_LIMIT_PER_MINUTE` mensajes por minuto.
- **CA-R5.5** Documento ilegible (informado a administradores en el resumen de reindexación).
- **CA-R5.6** Respuestas más largas que el límite de un mensaje (se dividen).

#### RNF-06 — Observabilidad

- **CA-R6.1** Cada mensaje deja un evento de log estructurado con ID de solicitud, usuario, resultado (respondida / no encontrada / error / denegada), fragmentos usados con puntaje, latencia y tokens consumidos.
- **CA-R6.2** El texto de las preguntas solo se registra con el nivel de detalle más alto o si la configuración lo habilita.
- **CA-R6.3** El nivel de detalle se controla con `LOG_LEVEL`.

#### RNF-07 — Calidad del código

- **CA-R7.1** La lógica central tiene pruebas automatizadas que corren sin red ni API keys.
- **CA-R7.2** El proyecto tiene formateador, linter y verificación de tipos configurados, sin errores.
- **CA-R7.3** Módulos, clases y funciones públicas tienen docstrings; los comentarios explican el porqué.

### Entidades clave

- **Documento**: archivo de la base de conocimiento. Atributos: nombre (ruta relativa a la carpeta), formato, huella del contenido, versión, estado (indexado / ilegible / en cuarentena), número de fragmentos, fecha de indexación.
- **Fragmento**: porción de un documento. Atributos: texto, documento y versión de origen, sección, página, posición dentro del documento, vector de embedding.
- **Turno de conversación**: mensaje de un usuario o del asistente. Atributos: ID de usuario, rol, texto, fecha. Pertenece a un solo usuario.
- **Pregunta sin respuesta**: fecha, ID de usuario, pregunta original, pregunta reformulada, mejor puntaje.
- **Resumen de reindexación**: conteo de nuevos, actualizados, eliminados, sin cambios, ilegibles y sospechosos, con duración.
- **Usuario**: ID numérico de Telegram con rol colaborador o administrador, definido por configuración.

## Criterios de éxito *(obligatorio)*

### Resultados medibles

- **SC-001**: En el conjunto de evaluación, al menos el 80 % de las preguntas dentro de alcance se responden correctamente y con la fuente correcta.
- **SC-002**: El 100 % de las preguntas fuera de alcance (mínimo 3) reciben "no encontrado" sin datos inventados.
- **SC-003**: Las preguntas de seguimiento (mínimo 2) se responden correctamente usando el contexto.
- **SC-004**: Un operador sin conocimiento previo deja el bot funcionando siguiendo solo el README, con máximo 4 comandos después de configurar `.env`.
- **SC-005**: Una pregunta típica se responde en menos de 10 segundos con la configuración por defecto.
- **SC-006**: Un documento nuevo o actualizado queda disponible para preguntas en menos de 1 minuto después de `/reindex`, para documentos de hasta 50 páginas.
- **SC-007**: Cero credenciales en el código y en el historial de git.
- **SC-008**: El costo por consulta con la configuración por defecto queda calculado y documentado en `docs/CRITERIO_TECNICO.md`.

## Supuestos

- **S-01** La carpeta de documentos es **entrada confiable**: solo los administradores escriben en ella. Es la principal defensa contra la manipulación desde documentos.
- **S-02** Los documentos están mayoritariamente en español y las respuestas se dan en español.
- **S-03** Los cambios en la carpeta se reflejan al arrancar, con `/reindex` o con la reindexación programada; **no en el instante** en que se copia el archivo.
- **S-04** Un documento eliminado de la carpeta deja de usarse y citarse (el enunciado no lo define; responder con una política retirada es peor que no responder).
- **S-05** Todos los usuarios autorizados pueden consultar todos los documentos (sin permisos por documento).
- **S-06** El bot corre como un único proceso que consulta a Telegram periódicamente (*long polling*), sin URL pública ni servidor.
- **S-07** Volumen esperado en la prueba: decenas de documentos y pocos usuarios simultáneos; el escalamiento se analiza en `docs/CRITERIO_TECNICO.md`.
- **S-08** Los PDF tienen capa de texto; no se hace OCR.
- **S-09** El evaluador dispone de una API key de al menos uno de los proveedores soportados y acepta una descarga inicial del modelo local de embeddings.

## Fuera de alcance

| Elemento | Motivo |
|---|---|
| Recibir archivos, fotos, notas de voz u otros mensajes no textuales | El asistente es solo de texto; se rechazan con un mensaje claro. |
| Subir documentos a la base por Telegram | Abriría la carpeta confiable (S-01) a un canal externo. |
| Preguntas sobre archivos propios del usuario | Es otro producto (almacenamiento por usuario, privacidad). |
| Vigilancia automática de la carpeta | Archivos a medio copiar y ráfagas de eventos; se reemplaza por S-03. |
| Generar formularios diligenciados | Implicaría inventar datos (contradice RF-04). |
| Permisos por documento | Ver S-05; se discute como riesgo en `docs/CRITERIO_TECNICO.md`. |
| Respuestas en audio | Los lectores de pantalla del teléfono ya cubren la necesidad (RF-12). |
| Despliegue con *webhook* | Requiere servidor con HTTPS público (S-06). |
| OCR de documentos escaneados | Ver S-08. |
| Uso en grupos de Telegram | Ver CA-07.4. |

## Glosario

| Término | Significado |
|---|---|
| RAG | Retrieval-Augmented Generation: recuperar fragmentos relevantes y entregarlos al modelo para que construya la respuesta. |
| Fragmento (*chunk*) | Porción de un documento que se indexa y recupera de forma independiente. |
| Embedding | Vector numérico que representa el significado de un texto y permite buscar por similitud. |
| Umbral de similitud | Puntaje mínimo para considerar relevante un fragmento. |
| Prompt injection | Texto, del usuario o de un documento, que intenta que el modelo ignore sus instrucciones. |

## Registro de cambios

| Versión | Fecha | Cambio | Motivo |
|---|---|---|---|
| 1.0 | 2026-11-06 | Versión inicial generada con `/speckit-specify` a partir del enunciado y las notas de diseño. | — |
