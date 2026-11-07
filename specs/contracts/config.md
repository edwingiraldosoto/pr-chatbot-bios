# Contrato: configuración

> Fase 1 de `/speckit-plan`. Fuente única de verdad de los parámetros; `.env.example` y
> `config.yaml` (T-03) deben coincidir con esta tabla.

**Precedencia** (de mayor a menor): variable de entorno → `.env` → `config.yaml` → valor por
defecto. Las variables de entorno usan el nombre de la columna "Variable".

| Variable | Clave YAML | Tipo / rango | Por defecto | Dónde | Req. |
|---|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | secreto, obligatorio | — | `.env` | RNF-01 |
| `LLM_PROVIDER` | `llm.provider` | `anthropic` \| `openai` \| `gemini` | `anthropic` | yaml | CA-R1.4, CA-R1.6 |
| `LLM_MAX_TOKENS` | `llm.max_tokens` | int 50–4000 | 600 | yaml | RNF-01 |
| `LLM_REWRITE_MAX_TOKENS` | `llm.rewrite_max_tokens` | int 20–1000 | 100 | yaml | RF-05 |
| `LLM_TIMEOUT_SECONDS` | `llm.timeout_seconds` | float > 0 (tiempo total, reintentos incluidos) | 30 | yaml | CA-R5.2 |
| `LLM_MAX_RETRIES` | `llm.max_retries` | int 0–5 | 2 | yaml | CA-R5.1 |
| `LLM_ANTHROPIC_API_KEY` | — | secreto | — | `.env` | CA-R1.6 |
| `LLM_ANTHROPIC_MODEL` | `anthropic.model` | str | `claude-haiku-5-5` | yaml | CA-R1.6 |
| `LLM_ANTHROPIC_EFFORT` | `anthropic.effort` | `low` \| `medium` \| `high` \| `xhigh` \| `max` \| vacío (no se envía) | `low` | yaml | CA-R1.6 (R-05) |
| `LLM_OPENAI_API_KEY` | — | secreto | — | `.env` | CA-R1.6 |
| `LLM_OPENAI_MODEL` | `openai.model` | str | `gpt-5-mini` | yaml | CA-R1.6 |
| `LLM_OPENAI_BASE_URL` | `openai.base_url` | URL o vacío (API oficial) | vacío | yaml | CA-R1.4 |
| `LLM_OPENAI_TEMPERATURE` | `openai.temperature` | float 0–2 o vacío (no se envía) | vacío | yaml | CA-R1.6 |
| `LLM_OPENAI_REASONING_EFFORT` | `openai.reasoning_effort` | `minimal` \| `low` \| `medium` \| `high` \| vacío (no se envía) | `low` | yaml | CA-R1.6 |
| `LLM_OPENAI_TOKEN_PARAM` | `openai.token_param` | `auto` \| `max_tokens` \| `max_completion_tokens` | `auto` | yaml | CA-R1.4 |
| `LLM_GEMINI_API_KEY` | — | secreto | — | `.env` | CA-R1.6 |
| `LLM_GEMINI_MODEL` | `gemini.model` | str | `gemini-2.5-flash` | yaml | CA-R1.6 |
| `LLM_GEMINI_TEMPERATURE` | `gemini.temperature` | float 0–2 o vacío (no se envía) | 0.1 | yaml | CA-R1.6 |
| `LLM_API_KEY` | — | secreto; API key del proveedor activo si no tiene la suya | — | `.env` | RNF-01 (enunciado) |
| `LLM_MODEL` | `llm.model` | str; reemplaza el modelo del proveedor activo | vacío | entorno | RNF-01 (enunciado) |
| `LLM_BASE_URL` | `llm.base_url` | URL; reemplaza la URL del proveedor activo (solo `openai`) | vacío | entorno | RNF-01 (enunciado) |
| `LLM_TEMPERATURE` | `llm.temperature` | float 0–2; reemplaza la temperatura del proveedor activo | vacío | entorno | RNF-01 (enunciado) |
| `EMBEDDING_PROVIDER` | `embeddings.provider` | `local` | `local` | yaml | RF-02 |
| `EMBEDDING_MODEL` | `embeddings.model` | str | `intfloat/multilingual-e5-small` | yaml | RF-02 |
| `KNOWLEDGE_BASE_PATH` | `knowledge_base.path` | ruta | `./knowledge_base` | yaml | RF-01 |
| `CHUNK_SIZE` | `chunking.size` | int 200–4000 | 800 | yaml | RF-02 |
| `CHUNK_OVERLAP` | `chunking.overlap` | int ≥ 0 y < `CHUNK_SIZE` | 150 | yaml | RF-02 |
| `RETRIEVAL_TOP_K` | `retrieval.top_k` | int 1–20 | 5 | yaml | RF-03 |
| `SIMILARITY_THRESHOLD` | `retrieval.similarity_threshold` | float 0–1 | 0.80 | yaml | RF-04 |
| `SYSTEM_PROMPT_PATH` | `prompts.system_prompt_path` | ruta a archivo existente | `./prompts/system_prompt.md` | yaml | CA-R1.5 |
| `REWRITE_PROMPT_PATH` | `prompts.rewrite_prompt_path` | ruta a archivo existente | `./prompts/rewrite_prompt.md` | yaml | RF-05 |
| `HISTORY_LENGTH` | `history.length` | int 0–50 (0 = sin historial) | 6 | yaml | RF-05 |
| `ALLOWED_USER_IDS` | `access.allowed_user_ids` | lista de int (CSV en entorno) | vacía | `.env` o yaml | RF-07 |
| `ADMIN_USER_IDS` | `access.admin_user_ids` | lista de int (CSV en entorno) | vacía | `.env` o yaml | RF-07 |
| `RATE_LIMIT_PER_MINUTE` | `access.rate_limit_per_minute` | int ≥ 1 | 10 | yaml | CA-R5.4 |
| `DUPLICATE_WINDOW_SECONDS` | `access.duplicate_window_seconds` | float 0–60 (0 = desactivado) | 5 | yaml | CA-R5.7 |
| `LOG_LEVEL` | `logging.level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `INFO` | yaml | CA-R6.3 |
| `LOG_QUESTIONS` | `logging.log_questions` | bool | `false` | yaml | CA-R6.2 |
| `DATA_DIR` | `storage.data_dir` | ruta | `./data` | yaml | RF-02 |
| `REINDEX_SCHEDULE_TIME` | `reindex.schedule_time` | `HH:MM` o vacío | vacío | yaml | RF-11 |
| `REINDEX_TIMEZONE` | `reindex.timezone` | zona IANA | `America/Bogota` | yaml | RF-11 |
| `REINDEX_NOTIFY_ADMINS` | `reindex.notify_admins` | bool | `true` | yaml | CA-11.3 |
| `INJECTION_CHECK` | `ingest.injection_check` | `off` \| `warn` \| `quarantine` | `warn` | yaml | RF-13 |
| `SOURCE_DOWNLOAD` | `bot.source_download` | bool | `false` | yaml | RF-14 |

**Perfiles por proveedor** (CA-R1.6). Cada proveedor tiene su sección en `config.yaml` y
su API key en `.env`, así que pueden convivir las tres. Cambiar de proveedor es cambiar una
línea (`LLM_PROVIDER`) y reiniciar. Para el proveedor activo, cada valor se resuelve así:

1. La variable genérica del enunciado (`LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`,
   `LLM_TEMPERATURE`), si está definida.
2. El valor de su perfil (`LLM_<PROVEEDOR>_…` o la sección del YAML).
3. El valor por defecto.

Las genéricas existen porque el enunciado las nombra; usarlas con varios proveedores tiene
un riesgo: un `LLM_MODEL` olvidado sigue aplicando al cambiar de proveedor. Por eso el
arranque registra de dónde salió cada valor y advierte si el modelo no corresponde al
proveedor (por ejemplo, `claude-…` con `gemini`).

"Vacío = no se envía": algunos modelos rechazan parámetros que otros aceptan (los GPT-5 solo
aceptan la temperatura por defecto; Haiku 4.5 rechaza `effort`; el SDK de Anthropic ya no
tiene `temperature`). Dejar el valor vacío hace que el parámetro no viaje en la petición,
sin tocar código.

**Verificación**: `python -m mesa_ayuda.llm.check` hace una llamada mínima con la
configuración actual e informa proveedor, modelo, latencia y tokens, o el error con el
parámetro a revisar. Se usa antes de reiniciar el bot con un proveedor nuevo.

**Reglas de validación** (fallo → el proceso no arranca y el mensaje nombra la variable):
- Secretos faltantes o vacíos: `TELEGRAM_BOT_TOKEN` y la API key del proveedor activo
  (`LLM_<PROVEEDOR>_API_KEY` o `LLM_API_KEY`). Las API keys de los proveedores inactivos
  son opcionales.
- Una API key en `config.yaml` (el archivo se versiona).
- `CHUNK_OVERLAP >= CHUNK_SIZE`.
- `SYSTEM_PROMPT_PATH` o `REWRITE_PROMPT_PATH` inexistentes.
- IDs de usuario no numéricos.
- `REINDEX_SCHEDULE_TIME` con formato inválido o zona horaria desconocida.

Advertencias (el proceso arranca): lista de autorizados y de admins vacías (CA-07.3);
carpeta de documentos inexistente o vacía.
