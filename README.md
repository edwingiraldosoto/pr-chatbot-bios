# Asistente de mesa de ayuda con RAG en Telegram

Bot de Telegram que responde preguntas de los colaboradores sobre políticas internas
(vacaciones, reembolso de gastos, soporte de TI, trabajo remoto…) **usando solo los
documentos de la carpeta `knowledge_base/`**. Cada respuesta termina con su fuente
(documento y sección o página). Si la información no está en los documentos, el bot lo
dice y no inventa.

Funciona con RAG (*Retrieval-Augmented Generation*):
1. Los documentos se fragmentan y se indexan con un modelo de embeddings local.
2. Ante cada pregunta se recuperan los fragmentos más parecidos.
3. Un modelo de lenguaje redacta la respuesta solo con esos fragmentos.

Agregar o cambiar un documento basta para que el bot responda sobre él, sin tocar código.

Prueba técnica para Servicios NubeVerde S.A.S., construida con *Spec Driven Development*:
la especificación de `specs/` se escribió antes que el código y se actualizó cada vez que
una decisión cambió.

## Contenido

1. [Requisitos previos](#requisitos-previos)
2. [Instalación y ejecución](#instalación-y-ejecución)
3. [Configuración paso a paso](#configuración-paso-a-paso)
4. [Proveedores de LLM soportados](#proveedores-de-llm-soportados)
5. [Uso](#uso)
6. [Agregar o actualizar documentos](#agregar-o-actualizar-documentos)
7. [Ejecución con Docker](#ejecución-con-docker)
8. [Operación](#operación)
9. [Pruebas y evaluación](#pruebas-y-evaluación)
10. [Limitaciones conocidas](#limitaciones-conocidas)
11. [Mapa de la documentación](#mapa-de-la-documentación)

## Requisitos previos

- **Python 3.11 o superior** (probado con 3.11.9 en Windows 11). En Windows, al instalar
  Python marca "Add python.exe to PATH".
- **Una cuenta de Telegram** para crear el bot.
- **Una API key de un proveedor de LLM.** Por defecto se usa Anthropic (Claude); también
  sirven OpenAI y Google Gemini (ver [proveedores](#proveedores-de-llm-soportados)). No
  hace falta ningún otro servicio pago: los embeddings se calculan en el equipo.
- **Unos 1,5 GB libres:** las dependencias ocupan unos 500 MB y el modelo de embeddings
  (465 MB) se descarga solo la primera vez que arranca el bot.
- **Conexión a internet** para Telegram, el LLM y la primera descarga del modelo.

## Instalación y ejecución

Primero crea y llena el `.env` (siguiente sección). Después son **tres comandos**, desde la
carpeta del proyecto:

**Windows (PowerShell o CMD)**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\python -m mesa_ayuda
```

**macOS y Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m mesa_ayuda
```

Al arrancar, el bot:
1. Lee y valida la configuración. Si falta algo, se detiene con un mensaje que nombra la
   variable.
2. Descarga el modelo de embeddings la primera vez (unos minutos, según la conexión).
3. Indexa `knowledge_base/` y empieza a recibir mensajes.

Los logs salen en la consola, una línea JSON por evento. El evento `bot_starting` indica que
ya está listo. Para detenerlo, pulsa `Ctrl+C`.

Con Docker, la alternativa es un solo comando (ver [Docker](#ejecución-con-docker)).

## Configuración paso a paso

La configuración se divide en dos archivos:
- **`.env`**: solo secretos y datos de cada instalación. Está en `.gitignore`, nunca se
  sube.
- **`config.yaml`**: todo lo demás, versionado y con un comentario por parámetro.

Cualquier valor de `config.yaml` se puede sobrescribir desde `.env` con su nombre en
mayúsculas (por ejemplo `SIMILARITY_THRESHOLD=0.82`). La lista completa está en
[`.env.example`](.env.example).

### 1. Crear el bot con @BotFather

1. En Telegram, busca **@BotFather** (tiene la marca azul de verificado) y ábrelo.
2. Envía `/newbot`.
3. Escribe el nombre visible del bot, por ejemplo `Mesa de ayuda`.
4. Escribe el usuario del bot. Debe terminar en `bot`, por ejemplo `mesa_ayuda_prueba_bot`.
5. BotFather responde con un **token** de la forma `123456789:AA…`. Guárdalo: es la
   contraseña del bot.

### 2. Obtener tu ID numérico de Telegram

El bot solo atiende a los usuarios cuyo ID está en la configuración. Para saber el tuyo,
elige una de estas opciones:

- **Con el propio bot (recomendado):** completa los pasos 3 y 4 dejando `ALLOWED_USER_IDS`
  vacío, arranca el bot y escríbele cualquier cosa. Te responderá "No tienes acceso a este
  asistente. Tu ID de Telegram es 123456789…". Copia ese número en el `.env` y reinicia.
- **Con @userinfobot:** búscalo en Telegram y envía `/start`; te muestra tu `Id`. Es un
  bot de terceros y a veces no responde.

### 3. Crear el `.env`

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Abre `.env` y completa como mínimo:

```ini
TELEGRAM_BOT_TOKEN=123456789:AA...      # el token del paso 1
LLM_ANTHROPIC_API_KEY=sk-ant-...        # tu API key (o la de otro proveedor, ver abajo)
ALLOWED_USER_IDS=111111111,222222222    # quiénes pueden preguntar (separados por coma)
ADMIN_USER_IDS=111111111                # quiénes pueden usar /reindex (ya tienen acceso)
```

Si dejas las dos listas vacías, nadie tiene acceso: el bot lo avisa al arrancar.

### 4. Verificar el LLM antes de arrancar (opcional, recomendado)

```bash
.venv/bin/python -m mesa_ayuda.llm.check        # Windows: .venv\Scripts\python -m mesa_ayuda.llm.check
```

Muestra qué proveedor, modelo y parámetros quedaron activos, y de qué variable salió cada
uno. Luego hace una llamada mínima: responde `OK: … respondió en 950 ms` o te dice qué
revisar (key inválida, modelo inexistente, sin red).

### 5. (Opcional) Enlace de invitación

Para que otra persona use el bot sin conocer su ID por adelantado, define un código en
`.env`:

```ini
INVITE_CODE=nubeverde-evaluacion-cambia-esto   # 12 a 64 caracteres: letras sin tilde, números, _ y -
```

Reinicia el bot y envíale `/invitacion` desde una cuenta de administrador. Te responde con
un enlace del tipo `https://t.me/<tu_bot>?start=<código>`:
- Quien lo abra y toque **Iniciar** queda autorizado.
- Para retirar a una persona, usa `/revocar ID`.
- Para retirar a todos los invitados, cambia el código y reinicia.

Para proteger tus créditos hay topes en `config.yaml`, sección `access`:
- 50 preguntas por usuario al día.
- 300 preguntas en total al día.
- 1.000 caracteres por pregunta.
- 10 mensajes por minuto.

Los administradores no tienen tope. Además, conviene fijar un **límite de gasto mensual en
la consola del proveedor** (en Anthropic: console.anthropic.com, sección de límites de la
organización): esa protección no depende del código.

## Proveedores de LLM soportados

| Proveedor | `LLM_PROVIDER` | Key en `.env` | Modelo por defecto | Estado |
|---|---|---|---|---|
| Anthropic (Claude) | `anthropic` (por defecto) | `LLM_ANTHROPIC_API_KEY` | `claude-haiku-5-5` | Probado con la API real |
| OpenAI | `openai` | `LLM_OPENAI_API_KEY` | `gpt-5-mini` | Probado con transporte simulado; sin key real para probarlo |
| Google Gemini | `gemini` | `LLM_GEMINI_API_KEY` | `gemini-2.5-flash` | Probado con transporte simulado; sin key real para probarlo |
| Cualquier API compatible con OpenAI (Groq, Ollama, LM Studio, vLLM…) | `openai` + `LLM_OPENAI_BASE_URL` | La del servicio | El que indiques | Mismo adaptador que OpenAI |

**Para cambiar de proveedor**:
1. Pon su key en `.env`. Las tres pueden convivir; se usa la del proveedor activo.
2. Cambia `llm.provider` en `config.yaml` (o `LLM_PROVIDER` en `.env`).
3. Corre el comando de verificación.
4. Reinicia el bot.

No hace falta tocar código. Cada proveedor tiene en `config.yaml` su sección (`anthropic:`,
`openai:`, `gemini:`) con su modelo y parámetros. Un valor vacío significa "no se envía",
porque algunos modelos rechazan parámetros que otros aceptan.

Las variables genéricas del enunciado también funcionan y se aplican al proveedor activo:
`LLM_MODEL`, `LLM_API_KEY`, `LLM_TEMPERATURE` y `LLM_BASE_URL`. Si usas `LLM_MODEL`, revísala
al cambiar de proveedor; el bot avisa si el modelo no parece del proveedor elegido.

**Ejemplos:**

```ini
# Gemini
LLM_PROVIDER=gemini
LLM_GEMINI_API_KEY=AIza...

# Ollama local (sin costo), con un modelo que tengas descargado
LLM_PROVIDER=openai
LLM_OPENAI_API_KEY=ollama
LLM_OPENAI_BASE_URL=http://localhost:11434/v1
LLM_OPENAI_MODEL=llama3.1
LLM_OPENAI_REASONING_EFFORT=none
```

## Uso

Escríbele al bot en un chat privado (en grupos no responde).

**Ejemplos con los documentos de muestra**

| Pregunta | Respuesta (resumida) |
|---|---|
| ¿Cuántos días de vacaciones tengo al año? | 15 días hábiles por año completo… Fuentes: politica_vacaciones.md, sección "2. Días de vacaciones". |
| ¿y se pueden acumular? *(seguimiento)* | Sí, máximo 2 periodos… Fuentes: politica_vacaciones.md, sección "4. Acumulación de vacaciones". |
| ¿Cuál es el tope diario de alimentación en un viaje? | $60.000 por día… Fuentes: reembolso_gastos.pdf, página 2. |
| ¿Cómo restablezco mi contraseña? | Paso 1: Abre https://clave.nubeverde.example… Fuentes: soporte_ti.docx, sección "3. Restablecer la contraseña". |
| ¿Cuál es la capital de Australia? | No encontré esa información en los documentos disponibles… |

**Comandos**

| Comando | Quién | Qué hace |
|---|---|---|
| `/start` | Todos los autorizados | Bienvenida. Telegram lo envía solo al abrir el bot por primera vez |
| `/help` | Todos los autorizados | Cómo preguntar y la lista de comandos (los administradores ven también los suyos) |
| `/reset` | Todos los autorizados | Borra tu conversación: la siguiente pregunta no usa el contexto anterior |
| `/fuentes` | Todos los autorizados | Documentos indexados y su número de fragmentos |
| `/reindex` | Administradores | Procesa documentos nuevos, modificados y borrados, y responde un resumen |
| `/reindex full` | Administradores | Reconstruye el índice completo |
| `/invitacion` | Administradores | Muestra el enlace de invitación |
| `/invitados` | Administradores | Quién entró con el enlace y cuántas preguntas hizo hoy |
| `/revocar ID` | Administradores | Retira el acceso a un invitado |

A un saludo ("hola", "gracias") el bot contesta con cordialidad sin consultar al modelo.
Una foto, un audio o un archivo reciben "Solo puedo responder mensajes de texto…". Una
respuesta más larga de 4.096 caracteres se divide en varios mensajes.

## Agregar o actualizar documentos

1. Copia, reemplaza o borra archivos en `knowledge_base/`. Se aceptan subcarpetas.
2. Desde una cuenta de administrador, envía `/reindex`, o reinicia el bot.
3. El resumen dice qué cambió:
   ```text
   Reindexación terminada en 4,2 s: 1 nuevo, 1 actualizado, 0 eliminados, 3 sin cambios.
   Ilegibles: manual_vpn.pdf (no tiene texto extraíble (¿PDF escaneado?)).
   ```

**Qué se procesa**:
- Formatos aceptados: `.md`, `.txt` (UTF-8 o Windows-1252), `.pdf` con texto y `.docx`.
- Se ignoran los archivos ocultos, los temporales de Office (`~$*`) y las demás extensiones.
- Solo se procesan los archivos nuevos o con contenido distinto, comparados por huella
  SHA-256.
- Un documento borrado deja de citarse.
- Un PDF escaneado sin capa de texto se informa como ilegible (no hay OCR).

**Cómo citar mejor**: el bot cita la sección (encabezados de Markdown o estilos "Título"
de Word) o la página (PDF). Un documento con títulos claros produce citas más útiles.

**Reindexación automática**: define `reindex.schedule_time` en `config.yaml` (por ejemplo
`"03:00"`). Los administradores reciben el resumen solo si hubo cambios o errores.

**Texto sospechoso**: si un documento trae frases dirigidas al asistente ("ignora tus
instrucciones…"), el resumen lo advierte. Con `ingest.injection_check: quarantine`, ese
documento ni siquiera se indexa.

## Ejecución con Docker

Requiere Docker con Compose y el mismo `.env`:

```bash
docker compose up -d --build
docker compose logs -f bot
```

El índice, la base SQLite y el modelo de embeddings quedan en el volumen `bot-data`. La
carpeta `knowledge_base/` se monta desde el equipo, así que basta con editarla y enviar
`/reindex`. **No ejecutes a la vez el bot local y el contenedor con el mismo token:**
Telegram solo entrega los mensajes a un proceso.

```bash
docker compose exec bot python -m mesa_ayuda.llm.check            # verificar el LLM
docker compose exec bot python -m scripts.unanswered --salida -   # preguntas sin respuesta
docker compose down
```

**Los secretos no viven en la imagen.** El `.dockerignore` deja fuera el `.env`, y Compose
pasa sus valores al contenedor cuando lo crea. Por eso, después de cambiar el `.env`
(por ejemplo, al rotar el token o la API key), hay que recrear el contenedor:

```bash
docker compose up -d --force-recreate
```

`docker compose restart` no sirve para esto: reutiliza el contenedor con los valores
viejos. No hace falta reconstruir la imagen. Después de cambiar el código o los prompts,
en cambio, sí: `docker compose up -d --build`.

## Operación

- **Preguntas sin respuesta:** cada "no encontrado" se guarda con la fecha, el usuario, la
  pregunta, la pregunta reformulada y el mejor puntaje. Para revisarlas en Excel:
  ```bash
  .venv/bin/python -m scripts.unanswered --separador ";"   # crea data/preguntas_sin_respuesta.csv
  ```
  Son la lista de temas que faltan en la base de conocimiento.
- **Logs:** una línea JSON por evento, con `request_id`, `user_id`, resultado, latencia,
  tokens y fuentes. El texto de las preguntas solo aparece con `logging.log_questions: true`
  o `LOG_LEVEL=DEBUG`. El token y las API keys se redactan siempre.
- **Datos generados** (en `data/`, fuera de git): índice de Chroma, `manifest.json`,
  `bot.sqlite3` (historial, invitados, uso diario, preguntas sin respuesta) y el modelo
  descargado. Borrar `data/` no pierde documentos: al arrancar se reconstruyen el índice y el
  modelo, pero se pierde lo de `bot.sqlite3`.
- **Prompts:** `prompts/system_prompt.md` (reglas de respuesta) y
  `prompts/rewrite_prompt.md` (reformulación de seguimientos) se editan sin tocar código.
  Los cambios se aplican al reiniciar.

**Problemas frecuentes**

| Síntoma | Causa y solución |
|---|---|
| "No se pudo arrancar el bot: Falta la API key de anthropic…" | Pon la key del proveedor activo en `.env` |
| "Telegram rechazó TELEGRAM_BOT_TOKEN" | Token mal copiado o revocado: pide otro a @BotFather con `/token` |
| `Conflict: terminated by other getUpdates request` en el log | Hay dos procesos con el mismo token (otro bot local o el contenedor) |
| El bot dice "No tienes acceso" | Tu ID no está en `ALLOWED_USER_IDS` ni en `ADMIN_USER_IDS`; agrégalo y reinicia |
| "El servicio de respuestas no está disponible" | El proveedor falló o la key no es válida: corre `python -m mesa_ayuda.llm.check` |
| Un documento no aparece en `/fuentes` | Míralo en el resumen de `/reindex`: puede estar ilegible o en cuarentena |
| En Docker, "Falta TELEGRAM_BOT_TOKEN" y el contenedor se reinicia sin parar | El contenedor se creó con un `.env` incompleto o anterior: corrige el `.env` y ejecuta `docker compose up -d --force-recreate` |

## Pruebas y evaluación

```bash
.venv/bin/python -m pip install ".[dev]"
.venv/bin/python -m pytest                       # 629 pruebas, sin red ni API keys
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy                         # tipado estricto de src/ y scripts/
.venv/bin/python -m scripts.evaluate             # evaluación con el LLM real (unos centavos)
```

La evaluación (30 casos: dentro de alcance, seguimientos, fuera de alcance, prompt
injection y reformulación) y la calibración del umbral, la fragmentación y los prompts
están en [`docs/EVALUACION.md`](docs/EVALUACION.md).

## Limitaciones conocidas

- **Sin OCR:** un PDF escaneado se informa como ilegible.
- **Sin tablas estructuradas:** en `.docx` y PDF las tablas se leen como texto corrido.
  Responden bien a preguntas puntuales, peor a comparaciones entre filas.
- **Solo texto:** no entiende audios ni imágenes. Las respuestas son texto plano, pensadas
  también para lectores de pantalla.
- **Sin permisos por documento:** todo usuario autorizado puede consultar todos los
  documentos.
- **Umbral calibrado con pocos documentos:** el valor por defecto (0,80) se midió con los
  documentos de ejemplo. Con otra base conviene revisar las preguntas sin respuesta y, si
  hace falta, ajustarlo.
- **Un solo proceso:** pensado para decenas de documentos y pocos usuarios a la vez. Qué
  cambiaría para miles de documentos y cientos de usuarios está en
  [`docs/CRITERIO_TECNICO.md`](docs/CRITERIO_TECNICO.md).
- **Proveedores sin probar con API real:** OpenAI y Gemini se probaron con su SDK y un
  transporte simulado, no con una key real. El comando de verificación lo confirma en
  segundos.
- **Cambios no inmediatos:** un documento nuevo se ve después de `/reindex`, de la tarea
  programada o de reiniciar, no en el instante en que se copia.

## Mapa de la documentación

| Archivo | Para qué leerlo |
|---|---|
| [`specs/spec.md`](specs/spec.md) | Qué hace el sistema y para quién: historias, requisitos con criterios de aceptación, supuestos y lo que queda fuera |
| [`specs/plan.md`](specs/plan.md) | Cómo se construyó: arquitectura, tecnologías, flujos y registro de cambios del plan |
| [`specs/tasks.md`](specs/tasks.md) | Las tareas (T-01…) con su requisito, su estado y sus commits |
| [`specs/research.md`](specs/research.md), [`specs/data-model.md`](specs/data-model.md), [`specs/contracts/`](specs/contracts/), [`specs/quickstart.md`](specs/quickstart.md) | Investigación de alternativas, modelo de datos, contratos (configuración, comandos, LLM, prompt) y escenarios de prueba manual |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Diagrama de componentes, flujo de ingesta, flujo de una pregunta con el prompt final y estructura de carpetas |
| [`docs/DECISIONES.md`](docs/DECISIONES.md) | Registro de decisiones: contexto, opciones, decisión, justificación y consecuencias |
| [`docs/EVALUACION.md`](docs/EVALUACION.md) | Conjunto de evaluación, resultados y calibración |
| [`docs/CRITERIO_TECNICO.md`](docs/CRITERIO_TECNICO.md) | Lo más difícil, qué cambiaría, producción, riesgos, costo por consulta y medición de valor |
| [`docs/USO_IA.md`](docs/USO_IA.md) | Cómo se usó la IA: herramientas, flujo, prompts, sugerencias rechazadas y verificación |
| [`prompts/`](prompts/) | Prompt de sistema y de reformulación |
| [`config.yaml`](config.yaml), [`.env.example`](.env.example) | Todos los parámetros, comentados |

Los documentos de `knowledge_base/` son de una empresa ficticia (NubeVerde S.A.S.) y se
redactaron con ayuda de IA.
