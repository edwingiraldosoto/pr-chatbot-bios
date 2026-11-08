# Manual técnico

> Cómo funciona el sistema implementado en este repositorio, de principio a fin: por dónde
> entra la información, cómo se procesa, dónde se guarda, qué configuración usa, cómo se
> ejecuta y cómo se despliegan los cambios. Todo lo que aparece aquí se comprobó en el
> código. Lo que es una deducción va marcado como **Inferencia**. El porqué de cada decisión
> está en [DECISIONES.md](DECISIONES.md); la calidad medida, en [EVALUACION.md](EVALUACION.md).

## Contenido

1. [Introducción](#1-introducción)
2. [Arquitectura general](#2-arquitectura-general)
3. [Flujo completo de una consulta](#3-flujo-completo-de-una-consulta)
4. [Configuración y variables de entorno](#4-configuración-y-variables-de-entorno)
5. [ChromaDB y almacenamiento vectorial](#5-chromadb-y-almacenamiento-vectorial)
6. [SQLite y persistencia](#6-sqlite-y-persistencia)
7. [Prompts y comportamiento de la IA](#7-prompts-y-comportamiento-de-la-ia)
8. [Proveedores de IA y API keys](#8-proveedores-de-ia-y-api-keys)
9. [Docker y despliegue](#9-docker-y-despliegue)
10. [Operación y mantenimiento](#10-operación-y-mantenimiento)
11. [Cambios frecuentes: dónde modificar cada cosa](#11-cambios-frecuentes-dónde-modificar-cada-cosa)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Inventario técnico](#13-inventario-técnico)

---

## 1. Introducción

**Para qué sirve.** Es un asistente de mesa de ayuda en Telegram. Responde preguntas de los
colaboradores sobre políticas internas (vacaciones, reembolsos, soporte de TI, trabajo
remoto…) **solo con lo que dicen los documentos** de la carpeta `knowledge_base/`, y cita
de qué documento sale cada respuesta.

**Qué problema resuelve.** Las respuestas existen, pero están repartidas en documentos que
nadie consulta. Con el bot, agregar o actualizar un archivo en la carpeta basta para que
pueda responder sobre él, sin tocar código.

**Componentes principales.**

| Componente | Qué es | Dónde corre |
|---|---|---|
| Bot de Telegram | `python-telegram-bot` en modo *long polling* | Proceso de Python (local o en el contenedor) |
| Ingesta e indexación | Lectores por formato, fragmentador y versionado de documentos | Mismo proceso |
| Embeddings | Modelo local `intfloat/multilingual-e5-small` con `fastembed` (ONNX, CPU) | Mismo proceso |
| Índice vectorial | ChromaDB embebido, persistente en disco | Mismo proceso, archivos en `data/chroma/` |
| Base relacional | SQLite (`data/bot.sqlite3`) | Mismo proceso |
| Modelo de lenguaje | Anthropic (por defecto), OpenAI o Gemini, por su API | **Fuera**: servicio del proveedor en internet |
| Telegram | Servidores de Telegram (Bot API) | **Fuera**: internet |

**Cómo interactúan, en resumen.** Al arrancar, el bot lee la configuración, indexa los
documentos (los convierte en fragmentos y vectores, que guarda en Chroma) y empieza a pedirle
mensajes a Telegram. Con cada pregunta:
1. Revisa que el usuario esté autorizado.
2. Busca en Chroma los fragmentos más parecidos.
3. Si ninguno alcanza el umbral, responde "no encontrado" sin gastar una llamada.
4. Si alguno lo alcanza, le pasa los fragmentos al modelo de lenguaje.
5. Devuelve la respuesta con la línea de fuentes armada por el código.

El historial, los cupos y las preguntas sin respuesta se guardan en SQLite.

---

## 2. Arquitectura general

```mermaid
flowchart LR
    subgraph Internet
        TG[Servidores de Telegram]
        LLM[API del proveedor de LLM<br/>Anthropic · OpenAI · Gemini]
        HF[Hugging Face<br/>descarga única del modelo]
    end
    subgraph Proceso["Proceso python -m mesa_ayuda (local o contenedor 'bot')"]
        MAIN[__main__.py<br/>main / build]
        CFG[config.py<br/>load_settings]
        H[bot/handlers.py<br/>BotHandlers]
        GU[guards.py]
        AS[answering.py<br/>AnswerService]
        RW[rewriter.py]
        RT[retrieval.py]
        PR[prompting.py]
        LP[llm/base.py<br/>RetryingProvider]
        IX[indexing/indexer.py<br/>Indexer]
        ING[ingest/<br/>discovery · loaders · chunker · injection_check]
        EMB[embeddings.py<br/>LocalEmbedder]
        VS[vectorstore.py<br/>VectorStore]
        ST[storage.py<br/>Storage]
    end
    subgraph Disco["Disco (data/ y carpetas versionadas)"]
        KB[(knowledge_base/)]
        CH[(data/chroma/)]
        MF[(data/manifest.json)]
        DB[(data/bot.sqlite3)]
        MOD[(data/models/)]
        PMT[(prompts/*.md)]
        YML[(config.yaml + .env)]
    end
    TG <-->|polling| H
    YML --> CFG --> MAIN
    MAIN --> H & IX & AS
    H --> GU
    H --> AS
    H --> ST
    AS --> RW --> LP
    AS --> RT --> EMB
    RT --> IX --> VS --> CH
    AS --> PR
    PMT --> AS
    AS --> LP --> LLM
    IX --> ING --> KB
    IX --> EMB
    IX --> MF
    EMB --> MOD
    HF -.primera vez.-> MOD
    ST --> DB
```

| Módulo (`src/mesa_ayuda/`) | Función |
|---|---|
| `__main__.py` | Punto de entrada. `main()` carga la configuración, configura los logs y llama a `build()`. `build()` arma las piezas, hace la indexación inicial (`indexer.reindex()`) y crea la `Application` de Telegram. Luego `run_polling()` |
| `config.py` | `load_settings()` lee `config.yaml` y `.env` y valida todo con pydantic (`Settings`). `Settings.llm` resuelve el perfil del proveedor activo |
| `logging_setup.py` | Logs JSON (structlog) a `stdout`, con los secretos redactados |
| `bot/handlers.py` | `BotHandlers`: filtro común (`gate`), comandos, preguntas (`question`), botón de descarga, errores (`on_error`), reindexación programada |
| `bot/messages.py` | Textos del bot y `split_message()` (límite de 4.096 caracteres de Telegram) |
| `bot/small_talk.py` | `classify()`: reconoce saludos, agradecimientos y despedidas |
| `guards.py` | `AccessControl`, `RateLimiter`, `DuplicateFilter`, `clean_question()`, invitaciones |
| `answering.py` | `AnswerService.answer()`: el flujo de la pregunta; citas (`extract_citations`, `format_sources`) |
| `rewriter.py` | `QuestionRewriter.rewrite()`: reformula seguimientos |
| `retrieval.py` | `Retriever.retrieve()`: vector de la pregunta, top-K y umbral |
| `prompting.py` | `build_answer_prompt()` y `build_rewrite_prompt()`: arman y escapan el prompt |
| `embeddings.py` | `LocalEmbedder` (fastembed) y `FakeEmbedder` (pruebas) |
| `vectorstore.py` | `VectorStore`: envoltura de Chroma (colección coseno, consulta filtrada por versión) |
| `indexing/indexer.py` | `Indexer`: indexación incremental con versiones, reconstrucción completa, candados |
| `indexing/manifest.py` | `load_manifest`, `save_manifest` (escritura atómica), `diff` |
| `ingest/` | `discovery.discover()`, `loaders.load_document()`, `chunker.chunk_sections()`, `injection_check.find_suspicious()` |
| `storage.py` | `Storage`: SQLite (historial, sin respuesta, invitados, uso diario) |
| `llm/` | `base.py` (interfaz, errores, `RetryingProvider`, `create_provider`), `anthropic.py`, `openai.py`, `gemini.py`, `fake.py` (pruebas), `check.py` (verificación) |

**Cómo se comunican.**
- Telegram y el bot se comunican por *long polling* (`Application.run_polling` con
  `allowed_updates=["message", "callback_query"]`, en `__main__.py`). El bot no expone
  puertos ni necesita una URL pública.
- El bot llama al LLM por HTTPS, con el SDK de cada proveedor.
- Todo lo demás son llamadas de Python dentro del mismo proceso, más lectura y escritura de
  archivos.

**Dentro y fuera de Docker.** Con `docker compose`, todo el proceso de la tabla anterior
corre dentro del contenedor `bot`. Fuera quedan:
- Telegram, el proveedor del LLM y Hugging Face (la descarga del modelo);
- la carpeta `knowledge_base/` del equipo, que se monta en el contenedor;
- el volumen `bot-data`, donde quedan los datos.

No hay otros servicios: Chroma y SQLite van **embebidos**, no son contenedores aparte.

---

## 3. Flujo completo de una consulta

```mermaid
sequenceDiagram
    participant U as Usuario (Telegram)
    participant H as handlers.py
    participant G as guards.py
    participant S as storage.py (SQLite)
    participant A as answering.py
    participant R as rewriter.py
    participant V as retrieval → indexer → Chroma
    participant L as llm/ (proveedor)
    U->>H: mensaje de texto
    H->>G: gate(): privado, acceso, copias, límite
    H->>H: question(): clean_question, classify (saludo)
    H->>S: _within_limits(): take_question (cupo diario)
    H->>S: history(user_id)
    H->>A: answer(texto, historial)
    A->>R: rewrite() si hay historial
    R->>L: complete() (reformulación)
    A->>V: retrieve(pregunta) → top 5 + umbral
    alt ningún fragmento ≥ umbral
        A-->>H: BELOW_THRESHOLD (sin llamar al LLM)
    else hay fragmentos relevantes
        A->>L: complete(system_prompt, historial + fragmentos + pregunta)
        L-->>A: texto con marcadores [n]
        A-->>H: Answer (texto + fuentes)
    end
    H->>S: add_turn() o record_unanswered()
    H-->>U: respuesta en texto plano + "Fuentes: …"
```

| # | Paso | Archivo y función | Qué pasa |
|---|---|---|---|
| 1 | Entrada del mensaje | `__main__.py` → `Application.run_polling(allowed_updates=ALLOWED_UPDATES)`; `bot/handlers.py` → `BotHandlers.register()` | python-telegram-bot recibe la actualización. Con `concurrent_updates(True)`, varias conversaciones se atienden en paralelo |
| 2 | Filtro común | `bot/handlers.py` → `BotHandlers.gate()` (grupo -1, corre antes que cualquier comando) | Ignora ediciones y chats que no son privados (`guards.is_private_chat`). Niega el acceso a quien no está autorizado (`is_authorized` → `AccessControl.is_authorized` o `Storage.is_invited`) y le muestra su ID. Descarta copias (`DuplicateFilter.is_duplicate`) y aplica el límite por minuto (`RateLimiter.check`). Si rechaza, lanza `ApplicationHandlerStop` |
| 3 | Invitación | `gate()` con `guards.invite_payload()` e `invite_matches()` → `Storage.invite()` | Un `/start <código>` correcto autoriza al usuario (solo si `INVITE_CODE` está definido) |
| 4 | Tipo de mensaje | `register()`: comandos → `start`, `help`, `reset`, `sources`, `reindex`, `invitation`, `invited`, `revoke`; comando desconocido → `unknown`; texto → `question`; otro tipo → `not_text` | Fotos, audios y stickers reciben "Solo puedo responder mensajes de texto…" |
| 5 | Limpieza y saludo | `BotHandlers.question()` → `guards.clean_question()`, `bot/small_talk.classify()` | Texto vacío → `not_text`. Saludo, gracias o despedida → respuesta fija, sin modelo |
| 6 | Topes | `BotHandlers._within_limits()` → `Storage.take_question()` | Largo máximo (`MAX_QUESTION_CHARS`) y cupo diario (`DAILY_QUESTIONS_PER_USER`, `DAILY_QUESTIONS_TOTAL`). El cupo se descuenta aquí, antes de llamar al modelo |
| 7 | Historial y orden | `question()` → candado por usuario `_lock()`; `BotHandlers._answer()` → `Storage.history(user_id)` | Los mensajes de una misma persona se atienden en orden. Se leen sus últimos `HISTORY_LENGTH` mensajes |
| 8 | Reformulación | `answering.AnswerService.answer()` → `rewriter.QuestionRewriter.rewrite()` → `prompting.build_rewrite_prompt()` | Solo si hay historial. Usa `prompts/rewrite_prompt.md` y `LLM_REWRITE_MAX_TOKENS` (100). Si falla o devuelve algo inservible (`_discard_reason`), se busca con la pregunta original |
| 9 | Embedding de la pregunta | `retrieval.Retriever.retrieve()` → `embeddings.LocalEmbedder.embed_query()` | Vector de 384 números del texto `"query: " + pregunta`. Corre en un hilo aparte (`asyncio.to_thread`) para no bloquear el bot |
| 10 | Búsqueda en ChromaDB | `Indexer.search()` → `vectorstore.VectorStore.query()` | Pide los `RETRIEVAL_TOP_K` (5) fragmentos más cercanos, filtrando `where={"doc_key": {"$in": versiones_activas}}`. La similitud es `1 − distancia coseno` |
| 11 | Umbral | `Retriever.retrieve()` (campo `relevant`) y `AnswerService.answer()` | Solo cuentan los fragmentos con similitud ≥ `SIMILARITY_THRESHOLD` (0,80). Si ninguno lo alcanza: resultado `BELOW_THRESHOLD` **sin llamar al LLM** |
| 12 | Contexto | `prompting.build_answer_prompt()` → `render_documents()`, `render_question()`, `normalize_history()` | Arma el prompt de sistema (contenido de `prompts/system_prompt.md`), el historial y un mensaje final con `<documentos>` (fragmentos numerados con fuente, sección y página) y `<pregunta>`. Todo texto se escapa (`escape_text`, `escape_attribute`) |
| 13 | Envío al proveedor | `llm/base.RetryingProvider.complete()` → `AnthropicProvider.complete()` (o el de OpenAI o Gemini) | `max_tokens = LLM_MAX_TOKENS` (600), tiempo total `LLM_TIMEOUT_SECONDS` (30 s) y hasta `LLM_MAX_RETRIES` (2) reintentos ante 429, 5xx o red |
| 14 | Respuesta cortada | `AnswerService.answer()` → reintento con `LLM_TRUNCATED_RETRY_MAX_TOKENS` (1200); `trim_truncated()` | Si el proveedor marca `truncated`, se pide una vez más con el tope mayor. Si vuelve a cortarse, se recorta a la última oración o palabra completa y el usuario ve un aviso |
| 15 | "No encontrado" del modelo | `answering.is_not_found()` | Si el texto empieza por `NO_ENCONTRADO`, el resultado es `LLM_NOT_FOUND` |
| 16 | Citas | `answering.extract_citations()`, `unique_sources()`, `format_sources()`, `Answer.render()` | Quita los marcadores `[n]` del texto y valida sus números. Arma `Fuentes: archivo, sección "…"` o `página N` con los metadatos de Chroma. Si el modelo no marcó ninguno, usa todos los fragmentos relevantes y lo registra (`citations_fallback`) |
| 17 | Guardado | `BotHandlers._answer()` → `Storage.add_turn()` o `Storage.record_unanswered()` | Una respuesta encontrada guarda el turno (sin la línea de fuentes). Un "no encontrado" va a la tabla `unanswered` |
| 18 | Respuesta a Telegram | `handlers.reply()` → `messages.split_message()` → `Message.reply_text()` | Texto plano, en partes si pasa de 4.096 caracteres. Con `SOURCE_DOWNLOAD=true` agrega un botón por documento (`_download_buttons`). Mientras espera, muestra "escribiendo…" (`_typing`) |
| 19 | Log | `question()` → evento `message_handled`; `AnswerService.answer()` → evento `answer_outcome` | Resultado, latencia, tokens, mejor puntaje, fragmentos usados y fuentes |

**Errores durante la consulta** (`BotHandlers._answer()` y `on_error()`):

| Excepción | Lo que ve el usuario (`bot/messages.py`) |
|---|---|
| `LLMTimeoutError` | `LLM_TIMEOUT`: "La respuesta está tardando más de lo normal…" |
| `LLMAuthError`, `LLMBadRequestError` | `LLM_UNAVAILABLE`: "El servicio de respuestas no está disponible…"; en el log, `llm_config_error` con nivel ERROR |
| `LLMError` (incluye `LLMUnavailableError`) | `LLM_UNAVAILABLE` |
| Cualquier otra | `UNEXPECTED_ERROR`: "Ocurrió un error inesperado…"; la traza solo va al log |

---

## 4. Configuración y variables de entorno

### Cómo se carga

- `config.load_settings("config.yaml")`, llamada en `__main__.main()`.
- **Precedencia:** variable de entorno del sistema → `.env` → `config.yaml` → valor por
  defecto en `Settings` (definida en `Settings.settings_customise_sources`).
- El `.env` se busca **en la carpeta de `config.yaml`**, no en la carpeta desde donde se
  lanza el bot.
- Las rutas relativas (`PATH_FIELDS`) se resuelven respecto a esa misma carpeta.
- Los **secretos** (`SECRET_FIELDS`) nunca se aceptan desde `config.yaml`: si aparecen
  ahí, el arranque falla.
- Una clave desconocida en `config.yaml` también hace fallar el arranque, para que un error
  de tipeo no se ignore en silencio.
- Cada parámetro de `config.yaml` se puede sobrescribir en `.env` con el nombre en
  mayúsculas. La tabla `YAML_PATHS` de `config.py` relaciona los dos nombres.
- En variables opcionales, un valor vacío (o `none` desde el entorno) significa "no se
  envía".

### Valores configurados en este equipo

El `.env` local define solo `TELEGRAM_BOT_TOKEN` `[REDACTADO]`, `LLM_ANTHROPIC_API_KEY`
`[REDACTADO]`, `ALLOWED_USER_IDS` (2 IDs) y `ADMIN_USER_IDS` (1 ID). `INVITE_CODE` está
comentado. Todo lo demás sale de `config.yaml`.

### Secretos y acceso (solo `.env` o entorno)

| Variable | Valor por defecto o ejemplo | Dónde se utiliza | Para qué sirve | Obligatoria |
|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Ejemplo en `.env.example`: `123456789:AAExampleTokenReplaceMe` | `__main__.build()` → `Application.builder().token(...)` | Conectar el bot con Telegram (lo entrega @BotFather) | **Sí** |
| `LLM_ANTHROPIC_API_KEY` | Ejemplo: `replace-with-your-anthropic-key` | `Settings._provider_key()` → `create_provider()` | Key de Anthropic | **Sí**, si `LLM_PROVIDER=anthropic` (el valor por defecto) |
| `LLM_OPENAI_API_KEY` | Sin valor | Ídem | Key de OpenAI o de la API compatible de `LLM_OPENAI_BASE_URL` | Sí, si `LLM_PROVIDER=openai` |
| `LLM_GEMINI_API_KEY` | Sin valor | Ídem | Key de Google Gemini | Sí, si `LLM_PROVIDER=gemini` |
| `LLM_API_KEY` | Sin valor | `Settings._provider_key()` (respaldo) | Genérica del enunciado: se usa si el proveedor activo no tiene su propia key | No |
| `INVITE_CODE` | Comentado. Ejemplo: `nubeverde-evaluacion-cambia-esto` (12 a 64 caracteres `A-Z a-z 0-9 _ -`) | `BotHandlers` (`invite_code`, `_invite_hash`) | Activa el enlace `https://t.me/<bot>?start=<código>` | No |
| `ALLOWED_USER_IDS` | Por defecto `[]`. Ejemplo: `111111111,222222222` | `AccessControl` en `__main__.build()` | IDs de Telegram autorizados. Si se define en `.env`, reemplaza `access.allowed_user_ids` | En la práctica sí: con las dos listas vacías nadie entra (falla cerrada) |
| `ADMIN_USER_IDS` | Por defecto `[]`. Ejemplo: `111111111` | `AccessControl` | Administradores: tienen acceso y pueden usar `/reindex`, `/invitacion`, `/invitados` y `/revocar` | Recomendada |

### Modelo de lenguaje

| Variable (`config.yaml`) | Valor por defecto | Dónde se utiliza | Para qué sirve | Obligatoria |
|---|---|---|---|---|
| `LLM_PROVIDER` (`llm.provider`) | `anthropic` | `Settings.llm`, `create_provider()` | Proveedor activo: `anthropic`, `openai` o `gemini` | No |
| `LLM_MAX_TOKENS` (`llm.max_tokens`) | `600` (rango 50–4000) | `AnswerService(max_tokens=...)` | Tope de salida de cada respuesta | No |
| `LLM_TRUNCATED_RETRY_MAX_TOKENS` (`llm.truncated_retry_max_tokens`) | `1200` (0–8000; si no es 0, debe ser mayor que `LLM_MAX_TOKENS`) | `AnswerService(retry_max_tokens=...)` | Tope del único reintento de una respuesta cortada; `0` lo desactiva | No |
| `LLM_REWRITE_MAX_TOKENS` (`llm.rewrite_max_tokens`) | `100` | `QuestionRewriter(max_tokens=...)` | Tope de la reformulación | No |
| `LLM_TIMEOUT_SECONDS` (`llm.timeout_seconds`) | `30` | `RetryingProvider`, clientes de cada SDK | Espera total por llamada, reintentos incluidos | No |
| `LLM_MAX_RETRIES` (`llm.max_retries`) | `2` (0–5) | `RetryingProvider` | Reintentos ante 429, 5xx o error de red | No |
| `LLM_MODEL` (`llm.model`) | vacío | `Settings.llm` | Genérica: reemplaza el modelo del proveedor activo, sea cual sea | No |
| `LLM_BASE_URL` (`llm.base_url`) | vacío | `Settings.llm` (solo openai) | Genérica: reemplaza `LLM_OPENAI_BASE_URL` | No |
| `LLM_TEMPERATURE` (`llm.temperature`) | vacío | `Settings.llm` | Genérica: reemplaza la temperatura del perfil. **Se ignora con anthropic** | No |
| `LLM_ANTHROPIC_MODEL` (`anthropic.model`) | `claude-haiku-5-5` | `AnthropicProvider` | Modelo de Claude | No |
| `LLM_ANTHROPIC_EFFORT` (`anthropic.effort`) | `low` | `AnthropicProvider.request()` → `output_config.effort` | Esfuerzo de razonamiento; vacío = no se envía | No |
| `LLM_ANTHROPIC_THINKING` (`anthropic.thinking`) | `disabled` | `AnthropicProvider.request()` → `thinking.type` | Que el razonamiento no gaste el tope de salida | No |
| `LLM_OPENAI_MODEL` (`openai.model`) | `gpt-5-mini` | `OpenAIProvider` | Modelo de OpenAI o de la API compatible | No |
| `LLM_OPENAI_BASE_URL` (`openai.base_url`) | vacío (API oficial) | `OpenAIProvider` | URL de una API compatible (Ollama, Groq, vLLM…) | No |
| `LLM_OPENAI_TEMPERATURE` (`openai.temperature`) | vacío (no se envía) | `OpenAIProvider.request()` | Temperatura; los GPT-5 solo aceptan la de fábrica | No |
| `LLM_OPENAI_REASONING_EFFORT` (`openai.reasoning_effort`) | `low` | `OpenAIProvider.request()` | `reasoning_effort`; vacío para modelos sin razonamiento | No |
| `LLM_OPENAI_TOKEN_PARAM` (`openai.token_param`) | `auto` | `OpenAIProvider.__init__` | `auto` usa `max_completion_tokens` con la API oficial y `max_tokens` con una URL propia | No |
| `LLM_GEMINI_MODEL` (`gemini.model`) | `gemini-2.5-flash` | `GeminiProvider` | Modelo de Gemini | No |
| `LLM_GEMINI_TEMPERATURE` (`gemini.temperature`) | `0.1` | `GeminiProvider.request()` | Temperatura de Gemini | No |

### Embeddings, documentos, fragmentación y búsqueda

| Variable (`config.yaml`) | Valor por defecto | Dónde se utiliza | Para qué sirve | Obligatoria |
|---|---|---|---|---|
| `EMBEDDING_PROVIDER` (`embeddings.provider`) | `local` (es el **único** valor aceptado) | Solo validación en `Settings` | Indica que los embeddings son locales | No |
| `EMBEDDING_MODEL` (`embeddings.model`) | `intfloat/multilingual-e5-small` | `LocalEmbedder` en `__main__.build()`; `Indexer._settings_snapshot()` | Modelo de vectores. Si cambia, el índice se reconstruye solo | No |
| `KNOWLEDGE_BASE_PATH` (`knowledge_base.path`) | `./knowledge_base` | `Indexer(knowledge_base=...)` → `discover()`; botón de descarga | Carpeta de documentos | No |
| `CHUNK_SIZE` (`chunking.size`) | `800` caracteres (200–4000) | `Indexer` → `chunk_sections()` | Tamaño máximo del fragmento, encabezado incluido. Si cambia, se reconstruye el índice | No |
| `CHUNK_OVERLAP` (`chunking.overlap`) | `150` (menor que `CHUNK_SIZE`) | Ídem | Caracteres repetidos entre fragmentos seguidos de una misma sección | No |
| `RETRIEVAL_TOP_K` (`retrieval.top_k`) | `5` (1–20) | `Retriever(top_k=...)` | Fragmentos que se piden a Chroma | No |
| `SIMILARITY_THRESHOLD` (`retrieval.similarity_threshold`) | `0.80` (0–1) | `Retriever(threshold=...)` | Similitud mínima para que un fragmento cuente | No |
| `INJECTION_CHECK` (`ingest.injection_check`) | `warn` | `Indexer._index_file()` → `find_suspicious()` | `off`, `warn` (indexa y advierte) o `quarantine` (no indexa). Si cambia, se reconstruye el índice | No |

### Prompts, conversación, límites y operación

| Variable (`config.yaml`) | Valor por defecto | Dónde se utiliza | Para qué sirve | Obligatoria |
|---|---|---|---|---|
| `SYSTEM_PROMPT_PATH` (`prompts.system_prompt_path`) | `./prompts/system_prompt.md` | `__main__.build()` → `AnswerService(system_prompt=...)` | Prompt de la respuesta. El archivo debe existir o el bot no arranca | No |
| `REWRITE_PROMPT_PATH` (`prompts.rewrite_prompt_path`) | `./prompts/rewrite_prompt.md` | `__main__.build()` → `QuestionRewriter` | Prompt de la reformulación | No |
| `HISTORY_LENGTH` (`history.length`) | `6` (0–50) | `Storage(history_length=...)` | Mensajes que se recuerdan por usuario; `0` = sin historial | No |
| `RATE_LIMIT_PER_MINUTE` (`access.rate_limit_per_minute`) | `10` | `RateLimiter` | Mensajes por usuario por minuto (en memoria) | No |
| `DUPLICATE_WINDOW_SECONDS` (`access.duplicate_window_seconds`) | `5` | `DuplicateFilter` | Ventana del filtro de copias; `0` lo apaga | No |
| `DAILY_QUESTIONS_PER_USER` (`access.daily_questions_per_user`) | `50` | `BotHandlers._within_limits()` | Tope diario por usuario (los administradores no tienen) | No |
| `DAILY_QUESTIONS_TOTAL` (`access.daily_questions_total`) | `300` | Ídem | Tope diario de todos | No |
| `MAX_QUESTION_CHARS` (`access.max_question_chars`) | `1000` | Ídem | Largo máximo de una pregunta | No |
| `LOG_LEVEL` (`logging.level`) | `INFO` | `configure_logging()` | `DEBUG`, `INFO`, `WARNING` o `ERROR` | No |
| `LOG_QUESTIONS` (`logging.log_questions`) | `false` | `BotHandlers(log_questions=...)` | Incluir el texto de la pregunta en `message_handled` | No |
| `DATA_DIR` (`storage.data_dir`) | `./data` | `__main__.build()` → `LocalEmbedder`, `Indexer`, `Storage` | Carpeta base de Chroma, manifest, SQLite y modelo | No |
| `REINDEX_SCHEDULE_TIME` (`reindex.schedule_time`) | vacío (apagada) | `Settings.schedule_time` → `schedule_daily_reindex()` | Hora diaria `HH:MM` de reindexación | No |
| `REINDEX_TIMEZONE` (`reindex.timezone`) | `America/Bogota` | Reindexación programada y día de los cupos | Zona horaria IANA | No |
| `REINDEX_NOTIFY_ADMINS` (`reindex.notify_admins`) | `true` | `BotHandlers._notify_admins()` | Avisar a los administradores si la reindexación programada tuvo cambios o errores | No |
| `SOURCE_DOWNLOAD` (`bot.source_download`) | `false` | `BotHandlers._download_buttons()` | Botón "Descargar <documento>" por fuente | No |
| `COMPANY_NAME` (`bot.company_name`) | `NubeVerde` | `messages.start_text()`, desde `BotHandlers` | Empresa que nombra la bienvenida de `/start`; vacío (o `none` en el entorno) = saludo sin nombre | No |

### Lo que no tiene variable propia

- **Ruta de ChromaDB:** siempre es `DATA_DIR/chroma` (`Indexer.__init__`:
  `data_dir / "chroma"`).
- **Ruta del manifest:** siempre es `DATA_DIR/manifest.json`.
- **Ruta de SQLite:** siempre es `DATA_DIR/bot.sqlite3` (`__main__.build()`).
- **Caché del modelo:** siempre es `DATA_DIR/models`.
- **Nombre de la colección de Chroma:** lo genera el código (`kb_<n>`).
- **Docker:** no tiene variables propias. `compose.yaml` pasa el `.env` completo con
  `env_file: .env`.

---

## 5. ChromaDB y almacenamiento vectorial

**Qué guarda.** Un registro por fragmento: el texto, su vector y metadatos para citar y
filtrar. Chroma **no** guarda los documentos originales ni el historial (eso es SQLite), y
**no** fragmenta: los fragmentos llegan ya cortados desde `ingest/chunker.py`.

| Campo | Ejemplo | Origen |
|---|---|---|
| `id` | `politica_vacaciones.md@3#2` | `ChunkRecord.id` = `<doc_key>#<chunk_index>` |
| `document` | `Política de vacaciones > 2. Días de vacaciones\nCada colaborador…` | Texto del fragmento, con la ruta de encabezados delante |
| `embedding` | 384 números | `LocalEmbedder.embed_documents()` |
| `source` | `politica_vacaciones.md` | Ruta relativa a `KNOWLEDGE_BASE_PATH` |
| `doc_key` | `politica_vacaciones.md@3` | Documento y versión; sirve para filtrar |
| `version` | `3` | Versión del documento en el manifest |
| `section` / `page` | `2. Días de vacaciones` / `0` | Del lector: título (md, docx) o página (pdf) |
| `chunk_index` | `2` | Posición dentro del documento |

**De dónde salen los documentos.** De `KNOWLEDGE_BASE_PATH` (`./knowledge_base`).
`ingest/discovery.discover()` recorre también las subcarpetas (sin seguir enlaces
simbólicos) y toma `.md`, `.txt`, `.pdf` y `.docx`. Ignora los archivos ocultos (`.`),
los temporales de Office (`~$…`) y las demás extensiones. A cada archivo le calcula su
SHA-256 (`file_sha256`).

**Cuándo se generan los chunks.** **Durante la indexación**, en
`Indexer._index_file()`, para cada documento nuevo o cambiado. El orden es:
1. `load_document()` lo convierte en secciones;
2. `chunk_sections(sections, CHUNK_SIZE, CHUNK_OVERLAP)` lo fragmenta;
3. `find_suspicious(chunks)` revisa el texto;
4. `embed_documents()` calcula los vectores;
5. `VectorStore.add()` lo guarda.

Los documentos que no cambiaron no se vuelven a fragmentar.

**Cómo se dividen** (`ingest/loaders.py` y `ingest/chunker.py`):
- **Secciones por formato:** Markdown por encabezados `#` (ignora los de bloques de código).
  DOCX por estilos de título (`Heading N`/`Título N`), con las tablas leídas por filas. PDF
  da una sección por página con `pypdf`. TXT es una sola sección (UTF-8 y, si falla,
  Windows-1252).
- **Corte jerárquico:** cada sección se parte primero por párrafo, luego por línea, oración
  y palabra, hasta caber en `CHUNK_SIZE=800` caracteres, contando el encabezado.
- **Solapamiento:** `CHUNK_OVERLAP=150` caracteres, empezando en un límite de palabra y
  solo dentro de la misma sección.

**Modelo de embeddings.** `intfloat/multilingual-e5-small` (384 dimensiones), con `fastembed`
sobre ONNX en CPU (`embeddings.LocalEmbedder`).
- Se configura en `EMBEDDING_MODEL` (`embeddings.model`).
- Se descarga una vez de Hugging Face a `DATA_DIR/models`.
- Antepone `passage: ` a los fragmentos y `query: ` a la pregunta (`_prefixes`).
- Solo acepta modelos del catálogo de fastembed o de `CUSTOM_MODELS`. Si el nombre no es
  válido, el bot no arranca (`EmbeddingModelError`).

**Cómo se define la colección** (`vectorstore.VectorStore.__init__`):
- `chromadb.PersistentClient(path=DATA_DIR/chroma, settings=Settings(anonymized_telemetry=False))`.
- `get_or_create_collection(name="kb_<n>", metadata={"hnsw:space": "cosine"}, embedding_function=None)`.
- Sin función de embeddings, para que Chroma no descargue su propio modelo.
- La colección en uso se anota en `manifest.json` (`collection`; hoy `kb_2`).

**Dónde se guarda.** En `DATA_DIR/chroma/`: el archivo `chroma.sqlite3` (metadatos internos
de Chroma; no es la base del bot) y una carpeta por índice HNSW. En Docker:
`/app/data/chroma` dentro del volumen `bot-data`. La ubicación la determina `DATA_DIR`.

**Cómo se consulta.** `Retriever.retrieve()` → `Indexer.search()`. Esa función toma un
candado de lectura para no cruzarse con un cambio de versión y llama a
`VectorStore.query(embedding, top_k, versiones_activas)`:

```python
# src/mesa_ayuda/vectorstore.py, VectorStore.query
where = {"doc_key": {"$in": sorted(active_doc_keys)}}
result = self._collection.query(query_embeddings=..., n_results=top_k, where=where, ...)
score = 1.0 - float(distance)
```

**Filtros y umbrales.**
- **Filtro de versión:** dentro de Chroma (`where` por `doc_key`).
- **Umbral:** se aplica después, en `Retriever.retrieve()`: `relevant = [hit for hit in
  hits if hit.score >= threshold]`.

**Cómo se actualizan o eliminan documentos** (`Indexer._incremental()`, `_index_file()`,
`_activate()` y `_remove()`):

| Situación | Cómo se detecta | Qué hace |
|---|---|---|
| Nuevo | No está en el manifest | Se indexa como versión 1 |
| Cambiado | Huella distinta | Agrega `archivo@vN+1`; bajo un candado de escritura cambia la versión activa y guarda el manifest; después borra `archivo@vN` |
| Igual | Misma huella | Se omite |
| Borrado | Está en el manifest y no en la carpeta | Sale de las versiones activas y se borran sus fragmentos |
| Ilegible | El lector falla | Queda `unreadable` en el manifest; si tenía una versión indexada, se retira |
| Sospechoso con `quarantine` | `find_suspicious()` encuentra algo | Queda `quarantined` y no se indexa |
| Fragmentos huérfanos | Restos de un proceso cortado | `VectorStore.delete_except()` los borra al final |

**Qué pasa al volver a indexar.**
- `/reindex`, el arranque y la reindexación diaria hacen lo incremental de la tabla
  anterior.
- Hay **reconstrucción completa** (`Indexer._rebuild()`: indexa todo en una colección nueva
  `kb_<n+1>`, cambia el puntero y borra las demás) en estos casos:
  - con `/reindex full`;
  - si cambia `EMBEDDING_MODEL`, `CHUNK_SIZE`, `CHUNK_OVERLAP` o `INJECTION_CHECK`;
  - si el manifest falta o está dañado;
  - si la colección no existe o tiene menos fragmentos de los esperados.
- Mientras tanto, el bot sigue respondiendo con el índice anterior.
- Solo corre una reindexación a la vez (`ReindexInProgressError`), y corre en un hilo
  aparte (`reindex_async` → `asyncio.to_thread`).

**El manifest** (`DATA_DIR/manifest.json`, `indexing/manifest.py`):
- Guarda `schema_version`, `settings` (modelo, tamaño, solapamiento, `injection_check`),
  `collection` y, por documento, `hash`, `version`, `status`, `chunks`, `indexed_at` y
  `warnings`.
- `save_manifest()` lo escribe en un temporal y lo reemplaza con `os.replace` (atómico).

---

## 6. SQLite y persistencia

**Para qué.** Para lo que tiene que sobrevivir a un reinicio y no es un documento ni un
vector: conversaciones, preguntas sin respuesta, invitados y consumo diario. Es
almacenamiento **relacional**, separado de Chroma.

**Dónde está.**
- Archivo `DATA_DIR/bot.sqlite3`; con la configuración actual, `data/bot.sqlite3`. En
  Docker, `/app/data/bot.sqlite3`.
- Lo crea `Storage.__init__` (crea la carpeta si falta) en `__main__.build()`.
- No hay variable para cambiar solo el nombre o la ruta del archivo: depende de `DATA_DIR`.
- Usa `PRAGMA journal_mode=WAL`, así que junto al archivo aparecen `bot.sqlite3-wal` y
  `bot.sqlite3-shm`.

**Cómo se accede desde el código** (`src/mesa_ayuda/storage.py`):
- Una sola conexión, `sqlite3.connect(path, check_same_thread=False, isolation_level=None)`,
  protegida con un `threading.Lock`.
- Las escrituras van en `BEGIN IMMEDIATE ... COMMIT` (`Storage._transaction`).
- Todas las consultas usan parámetros (`?`).
- El esquema se crea con `CREATE TABLE IF NOT EXISTS` (`SCHEMA`) al abrir la base.

| Tabla | Campos | Quién escribe y quién lee |
|---|---|---|
| `history` | `id`, `user_id`, `role` (`user` o `assistant`), `content`, `created_at` | `add_turn()` guarda y recorta a `HISTORY_LENGTH`; `history(user_id)` lee solo los del usuario; `reset(user_id)` borra (`/reset`) |
| `unanswered` | `id`, `created_at`, `user_id`, `question`, `rewritten`, `best_score`, `reason` (`below_threshold` o `llm_not_found`) | `record_unanswered()`; `unanswered(since=...)` lo lee (`scripts/unanswered.py`) |
| `invited_users` | `user_id` (PK), `first_name`, `code_hash`, `invited_at` | `invite()`, `is_invited()`, `revoke()` (`/revocar`), `invited_users()` (`/invitados`) |
| `daily_usage` | `day`, `user_id`, `questions` (PK `day` + `user_id`) | `take_question()` (cupo), `questions_on()` |

**Lo que no está en SQLite.** El límite por minuto (`RateLimiter`) y el filtro de copias
(`DuplicateFilter`) viven en memoria y se reinician con el proceso.

**Si se borra el archivo.** Se pierden:
- el historial de todas las conversaciones;
- las preguntas sin respuesta registradas;
- los invitados por enlace (tendrían que volver a abrir el enlace);
- el conteo de preguntas del día.

El bot crea una base vacía al arrancar. Los documentos y el índice no se afectan.

**Copia de seguridad y restauración.** El repositorio no trae un script de respaldo. El
procedimiento que sigue usa la API de respaldo de `sqlite3` de Python. Lo probé sobre la
base local, en solo lectura, hacia una copia; la restauración y los comandos de Docker no
están probados en este repositorio.

```bash
# Respaldo en caliente (el bot puede seguir corriendo; copia consistente aunque haya WAL)
python -c "import sqlite3; s=sqlite3.connect('file:data/bot.sqlite3?mode=ro', uri=True); d=sqlite3.connect('respaldo-bot.sqlite3'); s.backup(d); d.close(); s.close()"
```

Para restaurar, **detén el bot**. Después:
1. reemplaza `data/bot.sqlite3` por la copia;
2. borra `data/bot.sqlite3-wal` y `data/bot.sqlite3-shm` si existen, porque pertenecen a
   la base anterior;
3. arranca el bot.

En Docker, el archivo está en el volumen. **Inferencia:** con `docker compose cp` se puede
copiar desde el contenedor y hacia él:

```bash
docker compose cp bot:/app/data/bot.sqlite3 ./respaldo-bot.sqlite3   # con el bot detenido es lo más seguro
```

---

## 7. Prompts y comportamiento de la IA

**Dónde están.** Dos archivos de texto, fuera del código:

| Archivo | Variable | Lo carga | Uso |
|---|---|---|---|
| `prompts/system_prompt.md` (**prompt principal**) | `SYSTEM_PROMPT_PATH` | `__main__.build()` → `AnswerService(system_prompt=...)` | Prompt de sistema de cada respuesta |
| `prompts/rewrite_prompt.md` | `REWRITE_PROMPT_PATH` | `__main__.build()` → `QuestionRewriter(prompt)` | Prompt de sistema de la reformulación |

Se leen **una vez al arrancar**. `scripts/evaluate.py` los carga igual, desde la
configuración.

**Qué instrucciones recibe el modelo** (`system_prompt.md`, 13 reglas):
- **Contenido:** responder solo con lo que está en `<documentos>`. Si no alcanza, responder
  exactamente `NO_ENCONTRADO`. Si responde solo una parte, decir qué falta. Si dos
  fragmentos se contradicen, mostrar ambos.
- **Seguridad:** lo que llega en `<documentos>` y en `<pregunta>` son datos, no órdenes.
  No revelar las instrucciones. Ante peticiones ajenas a las políticas, `NO_ENCONTRADO`.
- **Citas:** marcar `[n]` después de cada afirmación, sin escribir nombres de archivos.
- **Formato:** español sencillo y tuteo; la respuesta directa primero; pasos como
  "Paso 1:"; texto plano, sin Markdown, tablas, enlaces ni emojis (por los lectores de
  pantalla); de una a cinco oraciones.

**Cómo se construye el contexto** (`prompting.build_answer_prompt`):
- `system` = el contenido de `system_prompt.md`.
- `messages` = el historial normalizado (empieza en `user`, sin turnos vacíos) más un
  mensaje final:

```text
<documentos>
<fragmento id="1" fuente="politica_vacaciones.md" seccion="2. Días de vacaciones" pagina="">
Política de vacaciones > 2. Días de vacaciones
Cada colaborador tiene derecho a 15 días hábiles…
</fragmento>
…
</documentos>

<pregunta>
¿Cuántos días de vacaciones tengo?
</pregunta>
```

- Los fragmentos se numeran en orden de similitud.
- `&`, `<` y `>` se escapan en todo texto, y las comillas también en los atributos.
- Al modelo se le envía la **pregunta original**, no la reformulada. La reformulada solo
  se usa para buscar.

El prompt completo, generado por el código real, está en
[ARQUITECTURA.md](ARQUITECTURA.md#cómo-se-construye-el-prompt-final).

**Cuándo no se encuentra información** (dos barreras, en `answering.AnswerService.answer`):
1. Ningún fragmento ≥ `SIMILARITY_THRESHOLD`: `Outcome.BELOW_THRESHOLD`, sin llamar al
   modelo.
2. El modelo responde `NO_ENCONTRADO` (`is_not_found()` acepta la palabra al inicio, sin
   importar mayúsculas ni puntuación) o una respuesta vacía: `Outcome.LLM_NOT_FOUND`.

En los dos casos el usuario recibe `messages.NOT_FOUND` y la pregunta queda en
`unanswered`.

**Cómo funcionan las citas** (`answering.extract_citations`, `format_sources`):
- El modelo escribe `[1]`, `[2][3]` o `[1, 2]`. El código reconoce los marcadores con la
  expresión `_MARKER`, los quita del texto y separa los números válidos (de 1 a la cantidad
  de fragmentos) de los inválidos.
- La línea `Fuentes: …` se arma **con los metadatos del índice** de los fragmentos citados,
  sin repetidos. El modelo nunca escribe nombres de archivo.
- Un número inválido se registra (`citation_invalid`). Si el modelo no marcó ninguno, se
  citan todos los fragmentos relevantes (`citations_fallback`).

**Proveedor y modelo:** ver la [sección 8](#8-proveedores-de-ia-y-api-keys).

**Para cambiar el comportamiento del bot:**

| Quiero… | Dónde |
|---|---|
| Cambiar tono, formato o reglas | `prompts/system_prompt.md` |
| Cambiar cómo se completan los seguimientos | `prompts/rewrite_prompt.md` |
| Que responda más o menos cosas | `SIMILARITY_THRESHOLD`, `RETRIEVAL_TOP_K` |
| Dar más contexto por fragmento | `CHUNK_SIZE` / `CHUNK_OVERLAP` (reconstruye el índice) |
| Respuestas más largas | `LLM_MAX_TOKENS` (y la regla 13 del prompt, "sé breve") |
| Textos fijos (bienvenida, ayuda, errores) | `src/mesa_ayuda/bot/messages.py` (es código) |

**Si cambias un prompt, revisa también:**
- `answering.py`: depende de la palabra exacta `NO_ENCONTRADO` (`NOT_FOUND_TOKEN`) y del
  formato `[n]` (`_MARKER`). Si el prompt deja de pedirlos, las citas y el "no encontrado"
  dejan de funcionar.
- `prompting.py`: las etiquetas `<documentos>`, `<fragmento>` y `<pregunta>` que menciona
  el prompt salen de aquí.
- `rewriter.py`: `_clean()` y `_discard_reason()` esperan una sola línea con una sola
  pregunta.
- Después corre `python -m scripts.evaluate` (con el LLM real, unos centavos) y compara con
  [EVALUACION.md](EVALUACION.md).

---

## 8. Proveedores de IA y API keys

**Implementados** (`src/mesa_ayuda/llm/`):

| Proveedor | Adaptador | SDK | Modelo por defecto | Parámetros que se envían |
|---|---|---|---|---|
| Anthropic (por defecto) | `AnthropicProvider` | `anthropic` | `claude-haiku-5-5` | `model`, `max_tokens`, `system`, `messages`, `output_config.effort` (`low`) y `thinking.type` (`disabled`). **Nunca** `temperature` |
| OpenAI o una API compatible | `OpenAIProvider` | `openai` | `gpt-5-mini` | `model`, `messages` (con el sistema como primer mensaje), `max_completion_tokens` o `max_tokens` según `LLM_OPENAI_TOKEN_PARAM`; `temperature` solo si está configurada; `reasoning_effort` (`low`) |
| Google Gemini | `GeminiProvider` | `google-genai` | `gemini-2.5-flash` | `model`, `contents`, `config` (`system_instruction`, `temperature`=0.1 y `max_output_tokens`). Los reintentos del SDK van apagados (`attempts=1`) |
| Falso (solo pruebas) | `FakeProvider` | — | — | Respuestas programadas |

Todos pasan por `RetryingProvider`, que aplica un tiempo total de `LLM_TIMEOUT_SECONDS` y
hasta `LLM_MAX_RETRIES` reintentos con espera exponencial solo ante 429, 5xx o errores de
red. Los errores de autenticación y los de petición no se reintentan.

**Cómo se elige el activo.**
- `LLM_PROVIDER` (`llm.provider`).
- `Settings.llm` resuelve el modelo, la key y los parámetros del perfil de ese proveedor,
  con las variables genéricas (`LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_BASE_URL`) por encima.
- `create_provider()` crea el adaptador.

**Dónde van las API keys.** En `.env`: `LLM_ANTHROPIC_API_KEY`, `LLM_OPENAI_API_KEY`,
`LLM_GEMINI_API_KEY` o la genérica `LLM_API_KEY`. Solo es obligatoria la del proveedor
activo (`Settings._check_llm_key`). Pueden estar las tres a la vez.

**Cambiar de proveedor.**
1. En `.env`, define `LLM_PROVIDER=openai` (o `gemini`) y su key.
2. Verifica con `python -m mesa_ayuda.llm.check`. Hace una llamada mínima y dice qué
   proveedor, modelo y parámetros quedaron activos y de qué variable salió cada uno. Sale
   con 0 si funciona, 1 si el proveedor rechazó la llamada y 2 si la configuración es
   inválida.
3. Reinicia el bot (en Docker, `docker compose up -d --force-recreate`).

**Cambiar de modelo.** El modelo de cada proveedor está en su perfil: `LLM_ANTHROPIC_MODEL`,
`LLM_OPENAI_MODEL` o `LLM_GEMINI_MODEL`. La genérica `LLM_MODEL` reemplaza el del proveedor
activo, sea cual sea. Si se te olvida al cambiar de proveedor, el arranque lo advierte
(`startup_warnings`). Después, verifica y reinicia.

**Si una API key se queda sin créditos.**
- **Lo que hace el código:**
  - un error 400 o 404 del proveedor → `LLMBadRequestError`, sin reintentos;
  - un 401 o 403 → `LLMAuthError`, sin reintentos;
  - un 429 → `TransientLLMError`: se reintenta y, al agotar los reintentos,
    `LLMUnavailableError`.

  En todos los casos el usuario ve "El servicio de respuestas no está disponible…" y el
  error queda en el log.
- **Inferencia (comportamiento del proveedor, no del código):** Anthropic suele responder
  a la falta de saldo con un 400 que dice que el saldo de créditos es muy bajo. OpenAI
  suele responder con un 429 `insufficient_quota`. El texto exacto aparece en el log
  (`llm_bad_request` o `llm_unavailable`) y en la salida de `python -m mesa_ayuda.llm.check`.
- **El bot no cambia de key ni de proveedor por su cuenta.** La conmutación automática se
  descartó a propósito ([CRITERIO_TECNICO.md](CRITERIO_TECNICO.md)).

**Otra API key sin perder datos.**
1. Cambia la key en `.env` (o cambia `LLM_PROVIDER` a un proveedor con saldo).
2. Verifica con `python -m mesa_ayuda.llm.check`.
3. Reinicia el bot. En Docker: `docker compose up -d --force-recreate`. **No** uses
   `docker compose down -v`.

El índice, el manifest y SQLite viven en `DATA_DIR` (o en el volumen `bot-data`) y no
dependen de la key. Cambiar de LLM no reindexa nada: los embeddings son locales y no
dependen del proveedor.

**Lo que el sistema permite hoy y lo que sería una mejora futura.**
- **Hoy:** un proveedor activo por configuración, cambio por variable y verificación con
  `llm.check`.
- **Mejora futura (no implementada):** conmutar automáticamente a un proveedor de respaldo
  cuando uno falla, o tener varias keys del mismo proveedor en rotación.

---

## 9. Docker y despliegue

**`Dockerfile`:**
- Base `python:3.11-slim`, `WORKDIR /app`.
- Instala primero las dependencias de `pyproject.toml`, en una capa que se reutiliza
  mientras `pyproject.toml` no cambie. Después copia `src/` e instala el paquete
  (`pip install --no-deps .`).
- **Copia dentro de la imagen** `config.yaml`, `prompts/`, `scripts/` y `knowledge_base/`.
- Crea el usuario sin privilegios `bot` (uid 10001), que solo puede escribir en `/app/data`.
- `VOLUME ["/app/data"]` y `CMD ["python", "-m", "mesa_ayuda"]`.
- `.dockerignore` deja fuera de la imagen `.env`, `data/`, `.git/`, `.venv/`, `tests/`,
  `specs/` y `docs/`, entre otros.

**`compose.yaml`:** un solo servicio, `bot`:

| Clave | Valor | Efecto |
|---|---|---|
| `name` | `pr-chatbot-nubeverde` | Nombre del proyecto en Docker (contenedor `pr-chatbot-nubeverde-bot-1`, volumen `pr-chatbot-nubeverde_bot-data`), sin importar el nombre de la carpeta |
| `build: .` / `image` | `mesa-ayuda-nubeverde` | Construye desde el `Dockerfile` de la raíz |
| `env_file` | `.env` | Pasa todas las variables del `.env` **al crear** el contenedor |
| `restart` | `always` | Arranca solo al crearlo, si se cae y cada vez que arranca Docker, aunque se haya detenido con Stop. Para apagarlo del todo: `docker compose down` |
| `volumes` | `./knowledge_base:/app/knowledge_base:ro` | La carpeta del equipo tapa la copia de la imagen. Basta editarla y enviar `/reindex` |
| `volumes` | `bot-data:/app/data` | Volumen con nombre: Chroma, manifest, SQLite y modelo |

**Dónde persisten los datos.**
- ChromaDB: `/app/data/chroma`, en el volumen `bot-data`.
- SQLite: `/app/data/bot.sqlite3`, en el mismo volumen.
- Sobreviven a `docker compose down`, `restart`, `--force-recreate` y `--build`. **Solo**
  `docker compose down -v` o `docker volume rm` los borran.

**Comandos** (comprobados contra `compose.yaml`; están en sus comentarios y en el README):

```bash
docker compose up -d --build              # construir (o reconstruir) la imagen y arrancar
docker compose logs -f bot                # ver los logs del servicio
docker compose exec bot python -m mesa_ayuda.llm.check            # verificar el LLM dentro del contenedor
docker compose exec bot python -m scripts.unanswered --salida -   # preguntas sin respuesta
docker compose up -d --force-recreate     # recrear el contenedor con el .env nuevo
docker compose restart bot                # solo reiniciar el proceso (mismo contenedor y mismas variables)
docker compose down                       # detener y borrar el contenedor (el volumen se conserva)
```

**Diferencias que importan:**

| Acción | Comando | Qué cambia | Cuándo usarla |
|---|---|---|---|
| Reiniciar el contenedor | `docker compose restart bot` | El proceso arranca de nuevo, con **la misma imagen y las mismas variables** | Reindexar al arrancar o salir de un estado raro. **No** aplica un `.env` nuevo |
| Recrear el contenedor | `docker compose up -d --force-recreate` | Contenedor nuevo con la misma imagen, que **vuelve a leer `.env`** | Después de modificar una variable de entorno (token, key, usuarios, proveedor, cualquier parámetro sobrescrito en `.env`) |
| Reconstruir la imagen | `docker compose up -d --build` (o `docker compose build` y después `up -d`) | Imagen nueva con el código, `config.yaml`, `prompts/` y `scripts/` actuales; luego un contenedor nuevo | Después de actualizar el código, `config.yaml` o un prompt |
| Modificar un prompt | Editar `prompts/*.md` y `docker compose up -d --build` | El prompt está **copiado dentro de la imagen**; editarlo en el equipo no cambia el contenedor hasta reconstruir | — |
| Modificar `config.yaml` | Igual que un prompt: `--build` | También está copiado en la imagen | Alternativa sin reconstruir: sobrescribir el parámetro en `.env` y usar `--force-recreate` |
| Actualizar el código | `docker compose up -d --build` | Imagen nueva | — |
| Cambiar documentos | Editar `knowledge_base/` y `/reindex` | Nada del contenedor: la carpeta está montada | — |

**Qué conservar al actualizar.** El volumen `bot-data` (índice, manifest, SQLite y modelo)
y tu `.env`. Ninguna de las acciones de la tabla los borra. Para empezar de cero:
`docker compose down -v`, que **borra el historial, los invitados y las preguntas sin
respuesta**.

**Avisos:**
- No corras el bot local y el contenedor a la vez con el mismo token: Telegram solo
  entrega los mensajes a un proceso.
- La primera vez el contenedor descarga el modelo al volumen (unos 450 MB).
- **Estado actual:** la imagen `mesa-ayuda-nubeverde` de este equipo se construyó el
  2026-11-08 con el código actual. Después de cambiar el código, hay que volver a correr
  `docker compose up -d --build`.
- Para que el bot arranque al prender el equipo, Docker Desktop también tiene que
  arrancar solo: *Settings → General → Start Docker Desktop when you sign in*.

---

## 10. Operación y mantenimiento

| Tarea | Cómo |
|---|---|
| ¿Está funcionando? | En el log debe aparecer `bot_starting` (proveedor, modelo, documentos y usuarios), seguido de `Application started`. En Telegram, `/start` debe responder. En Docker, `docker compose ps` muestra el estado del servicio |
| Consultar logs | Local: la consola donde corre `python -m mesa_ayuda` (JSON por línea en `stdout`; no se escriben en archivo). Docker: `docker compose logs -f bot`. Para seguir una conversación, filtra por `request_id` o `user_id` |
| Errores de conexión | `telegram_connection_error` (advertencia: python-telegram-bot reintenta solo); `llm_retry`, `llm_unavailable`, `llm_timeout` (proveedor); al arrancar, el mensaje "no hay conexión con Telegram" |
| Verificar las API keys | `python -m mesa_ayuda.llm.check` (Docker: `docker compose exec bot python -m mesa_ayuda.llm.check`) |
| Revisar el almacenamiento | Contenido de `DATA_DIR`: `chroma/`, `manifest.json`, `bot.sqlite3` (+ `-wal`, `-shm`), `models/`, `eval/`. En Docker: `docker volume inspect` sobre el volumen de compose |
| Estado de ChromaDB | En Telegram, `/fuentes` muestra cada documento con su estado y sus fragmentos. El evento `reindex_finished` resume la última indexación. En disco, `data/manifest.json` |
| Contar fragmentos en Chroma | Comando de abajo (lo probé sobre el índice de evaluación) |
| Estado de SQLite | Comando de abajo (lo probé sobre una copia) o `python -m scripts.unanswered --salida -` |
| Autorizar un usuario | Que le escriba al bot: la respuesta de acceso denegado le muestra su ID. Agrégalo a `ALLOWED_USER_IDS` en `.env` y reinicia (Docker: `--force-recreate`). Otra opción: activar `INVITE_CODE`, reiniciar y compartir el enlace que da `/invitacion` |
| Retirar un usuario | Invitado: `/revocar <id>`. Configurado: quitarlo de `.env` y reiniciar |
| Hacer respaldos | `data/bot.sqlite3` con el comando de la [sección 6](#6-sqlite-y-persistencia). El índice no hace falta respaldarlo: se reconstruye desde `knowledge_base/` |
| Recuperar desde un respaldo | Detener el bot, reemplazar `bot.sqlite3`, borrar `-wal` y `-shm`, arrancar ([sección 6](#6-sqlite-y-persistencia)) |
| Revisar preguntas sin respuesta | `python -m scripts.unanswered --separador ";"` crea `data/preguntas_sin_respuesta.csv` para Excel |

```bash
# Fragmentos por colección de Chroma (mejor con el bot detenido, para no abrir el índice desde dos procesos)
python -c "from pathlib import Path; from mesa_ayuda.vectorstore import collection_names, VectorStore; p=Path('data/chroma'); print({n: VectorStore(p, n).count() for n in collection_names(p)})"

# Filas por tabla de SQLite (en solo lectura)
python -c "import sqlite3; c=sqlite3.connect('file:data/bot.sqlite3?mode=ro', uri=True); print({t: c.execute(f'select count(*) from {t}').fetchone()[0] for (t,) in c.execute(\"select name from sqlite_master where type='table' and name not like 'sqlite_%'\")})"
```

Los dos comandos asumen `DATA_DIR=./data` y que se ejecutan desde la raíz del proyecto con
el entorno virtual activo.

---

## 11. Cambios frecuentes: dónde modificar cada cosa

"Reiniciar" en local es detener con `Ctrl+C` y volver a correr `python -m mesa_ayuda`. No
hay recarga en caliente.

| Si necesito cambiar... | Archivo o ubicación | Qué debo modificar | Qué debo ejecutar después |
|---|---|---|---|
| Token de Telegram | `.env` | `TELEGRAM_BOT_TOKEN` (de @BotFather, `/token`) | Local: reiniciar. Docker: `docker compose up -d --force-recreate` |
| API key | `.env` | `LLM_<PROVEEDOR>_API_KEY` o `LLM_API_KEY` | `python -m mesa_ayuda.llm.check`; reiniciar o `--force-recreate` |
| Proveedor de IA | `.env` (o `llm.provider` en `config.yaml`) | `LLM_PROVIDER` y la key de ese proveedor | `llm.check`; reiniciar. Docker: `--force-recreate` si fue en `.env`, `--build` si fue en `config.yaml` |
| Modelo de IA | `config.yaml` (`anthropic.model`, `openai.model`, `gemini.model`) o `.env` | `LLM_<PROVEEDOR>_MODEL` o `LLM_MODEL` | `llm.check`; reiniciar. Docker: `--build` si fue en `config.yaml`, `--force-recreate` si fue en `.env` |
| Prompt principal | `prompts/system_prompt.md` (o `SYSTEM_PROMPT_PATH`) | Las reglas, conservando `NO_ENCONTRADO` y `[n]` | Reiniciar. Docker: `docker compose up -d --build`. Recomendado: `python -m scripts.evaluate` |
| Umbral de similitud | `config.yaml` → `retrieval.similarity_threshold` | `SIMILARITY_THRESHOLD` (0–1; hoy 0.80) | Reiniciar (no reindexa). Docker: `--build` o, si va en `.env`, `--force-recreate`. Para probar antes: `python -m scripts.evaluate --solo-recuperacion --umbral 0.83` |
| Tamaño de chunks | `config.yaml` → `chunking.size` / `chunking.overlap` | `CHUNK_SIZE` / `CHUNK_OVERLAP` | Reiniciar: el índice se **reconstruye solo** al arrancar. Para probar antes: `python -m scripts.evaluate --solo-recuperacion --fragmento 600 --solapamiento 100` |
| Modelo de embeddings | `config.yaml` → `embeddings.model` | `EMBEDDING_MODEL` (del catálogo de fastembed o de `CUSTOM_MODELS`) | Reiniciar: descarga el modelo y reconstruye el índice. Revisar el umbral, que depende del modelo |
| Ruta de ChromaDB | `config.yaml` → `storage.data_dir` | `DATA_DIR` (Chroma queda en `DATA_DIR/chroma`; no tiene variable propia) | Reiniciar. Si la carpeta nueva está vacía, se reindexa todo y se empieza con un SQLite vacío |
| Configuración de SQLite | `config.yaml` | Ruta: `DATA_DIR` (archivo fijo `bot.sqlite3`). Historial: `HISTORY_LENGTH`. Cupos: `DAILY_QUESTIONS_*` | Reiniciar. Con otro `DATA_DIR`, usar `python -m scripts.unanswered --db <ruta>` |
| Usuarios autorizados | `.env` | `ALLOWED_USER_IDS` / `ADMIN_USER_IDS` (IDs separados por comas) | Reiniciar. Docker: `--force-recreate`. Invitados: `/revocar <id>` sin reiniciar |
| Código del bot | `src/mesa_ayuda/` | El módulo que toque | `python -m pytest`, `ruff check .`, `mypy`; reiniciar (con `pip install .` si cambian dependencias). Docker: `docker compose up -d --build` |
| Configuración Docker | `Dockerfile`, `compose.yaml`, `.dockerignore` | Imagen, volúmenes o política de reinicio | `docker compose up -d --build`. Cambiar el nombre del volumen deja los datos en el volumen anterior |
| Documentos | `knowledge_base/` | Agregar, reemplazar o borrar archivos | `/reindex` (administrador), reiniciar o esperar la reindexación diaria |

---

## 12. Solución de problemas

| Síntoma | Causa probable | Cómo comprobarlo | Cómo resolverlo | Archivo a revisar |
|---|---|---|---|---|
| "No se pudo arrancar el bot: … Falta TELEGRAM_BOT_TOKEN" (código de salida 2) | No hay `.env`, o le falta el token | Que exista `.env` junto a `config.yaml` | Copia `.env.example` a `.env` y completa el token | `.env`, `config.py` (`load_settings`) |
| "Falta la API key de anthropic: define LLM_ANTHROPIC_API_KEY…" | El proveedor activo no tiene key | `LLM_PROVIDER` y las keys en `.env` | Agrega la key de ese proveedor | `.env`, `config.py` (`_check_llm_key`) |
| "config.yaml: clave desconocida '…'" | Clave mal escrita en el YAML | El mensaje la nombra | Corrígela según `YAML_PATHS` o `specs/contracts/config.md` | `config.yaml` |
| "Telegram rechazó TELEGRAM_BOT_TOKEN" | Token mal copiado o revocado | `startup_failed` con `token rechazado` en el log | Pide otro a @BotFather y actualiza `.env` | `.env`, `__main__.py` (`InvalidToken`) |
| `Conflict: terminated by other getUpdates request` | Dos procesos con el mismo token (local y Docker, o dos consolas) | `docker compose ps` y los procesos de Python | Deja uno solo | `compose.yaml` |
| En Docker, "Falta TELEGRAM_BOT_TOKEN" y el contenedor se reinicia sin parar | El contenedor se creó con un `.env` incompleto o anterior; `restart` no lo vuelve a leer | `docker compose logs bot` | Corrige el `.env` y ejecuta `docker compose up -d --force-recreate` | `.env`, `compose.yaml` |
| "No tienes acceso a este asistente. Tu ID de Telegram es …" | El ID no está en `ALLOWED_USER_IDS` ni en `ADMIN_USER_IDS`, ni entró con invitación | `startup_warning` si las listas están vacías; `message_handled` con `outcome=denied` | Agrega el ID y reinicia, o comparte el enlace de `/invitacion` | `.env`, `guards.py` (`AccessControl`) |
| El bot no responde en un grupo | Solo atiende chats privados, a propósito | `message_ignored` con `reason=chat_not_private` | Escribirle por privado | `guards.is_private_chat` |
| "El servicio de respuestas no está disponible…" | Key inválida o sin créditos, modelo inexistente, proveedor caído | `llm_auth_error`, `llm_bad_request`, `llm_config_error` o `llm_unavailable` en el log | `python -m mesa_ayuda.llm.check`; corrige la key o el modelo, o cambia de proveedor | `.env`, `config.yaml`, `llm/*.py` |
| "La respuesta está tardando más de lo normal…" | El proveedor no respondió dentro de `LLM_TIMEOUT_SECONDS` | `llm_timeout` | Reintentar; subir `LLM_TIMEOUT_SECONDS` si se repite | `config.yaml`, `llm/base.py` |
| Responde "No encontré esa información" a algo que sí está | Puntaje bajo el umbral, documento no indexado o reformulación desviada | `answer_outcome`: `best_score`, `fragments`, `rewritten`; `/fuentes` | `/reindex` si el documento es nuevo; probar el umbral con `scripts.evaluate --solo-recuperacion`; revisar `rewrite_prompt.md` | `config.yaml`, `prompts/`, `retrieval.py` |
| Un documento no aparece en `/fuentes` o sale como ilegible | PDF escaneado o protegido, DOCX dañado, extensión no soportada, archivo temporal u oculto | Resumen de `/reindex`; `document_unreadable` y `file_skipped` en el log | Subir una versión con texto o en un formato soportado; `/reindex` | `ingest/loaders.py`, `ingest/discovery.py` |
| Advertencia de "texto sospechoso" en un documento | Frases como "ignora tus instrucciones" en el texto extraído (también texto oculto de un PDF o DOCX) | `document_suspicious` y el resumen de `/reindex` | Revisar el documento; con `INJECTION_CHECK=quarantine` no se indexa | `ingest/injection_check.py` |
| "Ya hay una reindexación en curso." | Otra reindexación (comando, arranque o programada) está corriendo | Eventos `reindex_started` sin su `reindex_finished` | Esperar a que termine | `indexing/indexer.py` |
| "No se pudo cargar el modelo de embeddings…" en el primer arranque | Sin internet para descargarlo, o disco lleno | `startup_failed`, `embedding_model_loading` | Conexión a internet la primera vez; espacio en `DATA_DIR/models` | `embeddings.py` |
| "EMBEDDING_MODEL …: el modelo '…' no está disponible" | Nombre fuera del catálogo de fastembed | El mensaje lista las opciones | Elegir uno de la lista | `config.yaml`, `embeddings.py` (`CUSTOM_MODELS`) |
| Respuesta con "La respuesta quedó incompleta. Pregúntame por un tema a la vez…" | El modelo se cortó dos veces (`LLM_MAX_TOKENS` y el reintento) | `answer_truncated_retry` y `answer_truncated` | Preguntar por un tema a la vez, o subir `LLM_TRUNCATED_RETRY_MAX_TOKENS` | `config.yaml`, `answering.py` |
| "Has enviado muchos mensajes seguidos…" o "Llegaste al máximo de … preguntas por hoy" | Límite por minuto o cupo diario | `outcome=rate_limited`, `quota_user` o `quota_total` | Esperar, o ajustar `RATE_LIMIT_PER_MINUTE` o `DAILY_QUESTIONS_*` | `config.yaml`, `guards.py`, `storage.py` |
| En Docker, un prompt o `config.yaml` editado no tiene efecto | Están copiados dentro de la imagen | Comparar el archivo del equipo con `docker compose exec bot cat prompts/system_prompt.md` | `docker compose up -d --build` | `Dockerfile` |
| `scripts.unanswered` no encuentra datos | Usa `data/bot.sqlite3` relativo a la carpeta actual, no `DATA_DIR` | `--db` | Correrlo desde la raíz, o con `--db <DATA_DIR>/bot.sqlite3` | `scripts/unanswered.py` |

---

## 13. Inventario técnico

| Tipo | Elemento | Para qué sirve |
|---|---|---|
| Lenguaje | Python ≥ 3.11 (`pyproject.toml`; la imagen usa 3.11-slim) | Todo el sistema |
| Librería | `python-telegram-bot[job-queue]` 22.x | Conexión con Telegram (polling), manejadores, JobQueue para la reindexación diaria |
| Librería | `chromadb` 1.x | Índice vectorial embebido y persistente |
| Librería | `fastembed` 0.9+ | Embeddings locales con ONNX en CPU |
| Librería | `pypdf` 7.x / `python-docx` 1.x | Lectura de PDF por página y DOCX por estilos |
| Librería | `pydantic` 2.x / `pydantic-settings` 2.x / `PyYAML` 6.x | Configuración tipada y validada desde `.env` y `config.yaml` |
| Librería | `structlog` 26.x | Logs JSON |
| Librería | `anthropic` 2.x / `openai` 3.x / `google-genai` 3.x | SDK de cada proveedor de LLM |
| Librería | `tzdata` | Zonas horarias en Windows |
| Librería (desarrollo) | `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `types-PyYAML` | Pruebas, lint y formato, tipado estricto |
| Servicio externo | Telegram Bot API | Entrada y salida de mensajes (gratis) |
| Servicio externo | Anthropic, OpenAI o Gemini | Generación de respuestas y reformulaciones (es lo único que tiene costo) |
| Servicio externo | Hugging Face | Descarga única del modelo de embeddings |
| Base de datos | ChromaDB en `DATA_DIR/chroma` | Vectores y fragmentos |
| Base de datos | SQLite en `DATA_DIR/bot.sqlite3` | Historial, sin respuesta, invitados, uso diario |
| Archivo de estado | `DATA_DIR/manifest.json` | Huellas, versiones y estado de cada documento |
| Modelo | `intfloat/multilingual-e5-small` (384 dim) | Embeddings |
| Modelo | `claude-haiku-5-5` (por defecto); `gpt-5-mini`, `gemini-2.5-flash` en los otros perfiles | Lenguaje |
| Configuración | `config.yaml` | Parámetros no secretos, comentados |
| Configuración | `.env` / `.env.example` | Secretos e IDs / plantilla con todas las variables |
| Configuración | `prompts/system_prompt.md`, `prompts/rewrite_prompt.md` | Comportamiento del modelo |
| Configuración | `pyproject.toml` | Dependencias y configuración de ruff, mypy y pytest |
| Configuración | `Dockerfile`, `compose.yaml`, `.dockerignore` | Contenedor |
| Configuración | `.gitignore` | Deja fuera de git `.env`, `data/`, entornos y cachés |
| Script | `python -m mesa_ayuda` | Arranca el bot |
| Script | `python -m mesa_ayuda.llm.check` | Verifica proveedor, modelo y key con una llamada real |
| Script | `python -m scripts.evaluate` | Evaluación reproducible (30 casos). Opciones: `--solo-recuperacion`, `--umbral`, `--top-k`, `--fragmento`, `--solapamiento`, `--salida`, `--config`, `--preguntas`. Usa su propio índice en `DATA_DIR/eval` |
| Script | `python -m scripts.unanswered` | Exporta las preguntas sin respuesta a CSV (`--db`, `--salida`, `--desde`, `--separador`) |
| Script | `python -m scripts.build_sample_docs` | Regenera el PDF y el DOCX de ejemplo de `knowledge_base/` |
| Script | `scripts/simple_pdf.py` | Escritor mínimo de PDF que usan el script anterior y las pruebas |
| Comando de Telegram | `/start`, `/help`, `/reset`, `/fuentes` | Usuarios |
| Comando de Telegram | `/reindex`, `/reindex full`, `/invitacion`, `/invitados`, `/revocar <id>` | Administradores |
