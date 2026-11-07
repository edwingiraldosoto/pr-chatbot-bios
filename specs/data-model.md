# Modelo de datos (fase 1)

> Generado con `/speckit-plan` (fase 1). Entidades del spec ("Entidades clave") llevadas a
> estructuras concretas.

## 1. Fragmento (ChromaDB, colección `kb_<n>`)

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | str | `"<doc_key>#<chunk_index>"`, único |
| `document` | str | Texto del fragmento (con el encabezado de sección antepuesto) |
| `embedding` | float[384] | Vector de `passage: <texto>` |
| `metadata.source` | str | Ruta relativa a `KNOWLEDGE_BASE_PATH`, con `/` (p. ej. `rrhh/vacaciones.md`) |
| `metadata.doc_key` | str | `"<source>@<version>"`; filtro de versión activa |
| `metadata.version` | int | Versión del documento (empieza en 1) |
| `metadata.section` | str | Título de la sección o `""` |
| `metadata.page` | int | Página (PDF) o `0` si no aplica |
| `metadata.chunk_index` | int | Posición dentro del documento |

Colección creada con `metadata={"hnsw:space": "cosine"}`. Similitud = `1 − distancia`.

## 2. Manifest (`data/manifest.json`)

```json
{
  "schema_version": 1,
  "settings": {
    "embedding_provider": "local",
    "embedding_model": "intfloat/multilingual-e5-small",
    "chunk_size": 800,
    "chunk_overlap": 150
  },
  "collection": "kb_1",
  "documents": {
    "politica_vacaciones.md": {
      "hash": "sha256:a3f9…",
      "version": 2,
      "status": "indexed",
      "chunks": 14,
      "indexed_at": "2026-11-07T10:12:00-05:00",
      "warnings": []
    }
  }
}
```

- `status` ∈ `indexed` | `unreadable` | `quarantined`. Solo `indexed` aporta `doc_key`
  activos a la búsqueda y aparece en `/fuentes`.
- Escritura atómica: se escribe `manifest.json.tmp` y se reemplaza con `os.replace`.
- Si `settings` difiere de la configuración actual, o el archivo falta o no se puede leer,
  se hace reconstrucción completa en una colección nueva (`kb_<n+1>`) y se borra la anterior.

### Transiciones de un documento

| Estado previo | Evento | Estado nuevo | Acción en el índice |
|---|---|---|---|
| (ninguno) | archivo nuevo legible | `indexed` v1 | agregar fragmentos v1 |
| (ninguno) | archivo nuevo ilegible | `unreadable` | — |
| `indexed` vN | contenido cambió, legible | `indexed` vN+1 | agregar vN+1 → activar → borrar vN |
| `indexed` vN | contenido cambió, ilegible | `unreadable` | desactivar → borrar vN (CA-09.6) |
| `indexed` vN | archivo eliminado | (se quita) | desactivar → borrar vN |
| cualquiera | modo `quarantine` y texto sospechoso | `quarantined` | desactivar → borrar |
| `unreadable` | contenido cambió, legible | `indexed` vN+1 | agregar → activar |

## 3. SQLite (`data/bot.sqlite3`)

```sql
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL           -- ISO 8601 con zona horaria
);
CREATE INDEX IF NOT EXISTS ix_history_user ON history (user_id, id);

CREATE TABLE IF NOT EXISTS unanswered (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    user_id     INTEGER NOT NULL,
    question    TEXT    NOT NULL,
    rewritten   TEXT,
    best_score  REAL,                       -- NULL si no se recuperó nada
    reason      TEXT    NOT NULL CHECK (reason IN ('below_threshold', 'llm_not_found'))
);
```

- Al guardar un turno se recorta el historial del usuario a los últimos `HISTORY_LENGTH`.
- El turno del asistente se guarda **sin** la línea de fuentes, para no contaminar la
  reformulación.
- `/reset` ejecuta `DELETE FROM history WHERE user_id = ?`.

Invitaciones y topes (CA-07.5, RNF-08):

```sql
CREATE TABLE IF NOT EXISTS invited_users (
    user_id     INTEGER PRIMARY KEY,
    first_name  TEXT    NOT NULL DEFAULT '',
    code_hash   TEXT    NOT NULL,          -- SHA-256 del INVITE_CODE con que entró
    invited_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    day         TEXT    NOT NULL,          -- AAAA-MM-DD en REINDEX_TIMEZONE
    user_id     INTEGER NOT NULL,
    questions   INTEGER NOT NULL,
    PRIMARY KEY (day, user_id)
);
```

## 4. Objetos en memoria

| Objeto | Campos | Uso |
|---|---|---|
| `Section` | `text`, `section`, `page` | Salida de los lectores |
| `Chunk` | `text`, `source`, `section`, `page`, `chunk_index` | Salida del fragmentador |
| `RetrievedChunk` | `Chunk` + `score` | Salida de la recuperación |
| `Answer` | `text`, `sources`, `outcome`, `usage` | Salida del servicio de respuesta |
| `ReindexSummary` | `new`, `updated`, `deleted`, `unchanged`, `unreadable[]`, `suspicious[]`, `full_rebuild`, `duration_s` | Respuesta de `/reindex` y aviso nocturno |
| `RateLimiter` | `dict[user_id, deque[timestamps]]` | Ventana deslizante de 60 s |
