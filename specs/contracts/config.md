# Contrato: configuración

> Fase 1 de `/speckit-plan`. Fuente única de verdad de los parámetros; `.env.example` y
> `config.yaml` (T-03) deben coincidir con esta tabla.

**Precedencia** (de mayor a menor): variable de entorno → `.env` → `config.yaml` → valor por
defecto. Las variables de entorno usan el nombre de la columna "Variable".

| Variable | Clave YAML | Tipo / rango | Por defecto | Dónde | Req. |
|---|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | secreto, obligatorio | — | `.env` | RNF-01 |
| `LLM_API_KEY` | — | secreto, obligatorio (salvo proveedor sin clave) | — | `.env` | RNF-01 |
| `LLM_PROVIDER` | `llm.provider` | `anthropic` \| `openai` \| `gemini` | `anthropic` | yaml | CA-R1.4 |
| `LLM_MODEL` | `llm.model` | str | `claude-haiku-5-5` | yaml | RNF-01 |
| `LLM_BASE_URL` | `llm.base_url` | URL o vacío (solo `openai`, para APIs compatibles) | vacío | yaml | CA-R1.4 |
| `LLM_TEMPERATURE` | `llm.temperature` | float 0–1 | 0.1 | yaml | RNF-01 |
| `LLM_MAX_TOKENS` | `llm.max_tokens` | int 50–4000 | 600 | yaml | RNF-01 |
| `LLM_TIMEOUT_SECONDS` | `llm.timeout_seconds` | float > 0 | 30 | yaml | CA-R5.2 |
| `LLM_MAX_RETRIES` | `llm.max_retries` | int 0–5 | 2 | yaml | CA-R5.1 |
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
| `LOG_LEVEL` | `logging.level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `INFO` | yaml | CA-R6.3 |
| `LOG_QUESTIONS` | `logging.log_questions` | bool | `false` | yaml | CA-R6.2 |
| `DATA_DIR` | `storage.data_dir` | ruta | `./data` | yaml | RF-02 |
| `REINDEX_SCHEDULE_TIME` | `reindex.schedule_time` | `HH:MM` o vacío | vacío | yaml | RF-11 |
| `REINDEX_TIMEZONE` | `reindex.timezone` | zona IANA | `America/Bogota` | yaml | RF-11 |
| `REINDEX_NOTIFY_ADMINS` | `reindex.notify_admins` | bool | `true` | yaml | CA-11.3 |
| `INJECTION_CHECK` | `ingest.injection_check` | `off` \| `warn` \| `quarantine` | `warn` | yaml | RF-13 |
| `SOURCE_DOWNLOAD` | `bot.source_download` | bool | `false` | yaml | RF-14 |

**Reglas de validación** (fallo → el proceso no arranca y el mensaje nombra la variable):
- Secretos faltantes o vacíos.
- `CHUNK_OVERLAP >= CHUNK_SIZE`.
- `SYSTEM_PROMPT_PATH` o `REWRITE_PROMPT_PATH` inexistentes.
- IDs de usuario no numéricos.
- `REINDEX_SCHEDULE_TIME` con formato inválido o zona horaria desconocida.

Advertencias (el proceso arranca): lista de autorizados y de admins vacías (CA-07.3);
carpeta de documentos inexistente o vacía.
