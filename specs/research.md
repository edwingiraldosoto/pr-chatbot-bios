# Investigación (fase 0): Asistente de mesa de ayuda con RAG en Telegram

> Generado con `/speckit-plan` (fase 0). Cada entrada resuelve una incógnita técnica del
> plan. La versión extendida, con el formato contexto → opciones → decisión →
> justificación → consecuencias, se escribe en `docs/DECISIONES.md` (T-42).

## R-01 — Lenguaje y librería de Telegram

- **Decisión**: Python 3.11+ con `python-telegram-bot` 22.x (async, *long polling*, JobQueue).
- **Justificación**: el enunciado sugiere Python; es el ecosistema más maduro para RAG.
  La librería es oficialmente async, trae JobQueue para la reindexación nocturna y el
  *polling* funciona sin URL pública (S-06).
- **Alternativas**: `aiogram` (excelente, pero sin planificador integrado y documentación
  mayormente en ruso); TypeScript con `grammY` (bueno para bots, pero ecosistema RAG local
  más pobre, sobre todo en embeddings locales); *webhook* (requiere servidor HTTPS).

## R-02 — Framework de orquestación vs implementación propia

- **Decisión**: implementación propia pequeña.
- **Justificación**: el flujo es lineal (recuperar → filtrar → prompt → LLM → citar) y el
  enunciado pide documentar cómo se construye el prompt final. Con código propio el prompt
  es visible y probable, sin capas de abstracción que cambian entre versiones.
- **Alternativas**: LangChain (muchas dependencias, el prompt final queda oculto en
  cadenas); LlamaIndex (bueno para ingesta, pero impone su modelo de nodos y su
  persistencia, y complica el cambio de versión por documento de RF-09).
- **Consecuencia**: hay que escribir los lectores, el fragmentador y los adaptadores de LLM
  (unas centenas de líneas, todas con pruebas).

## R-03 — Estrategia de fragmentación

- **Decisión**: corte jerárquico: sección (encabezado Markdown / estilo de título DOCX /
  página PDF) → párrafo → oración → caracteres, con `CHUNK_SIZE=800` y `CHUNK_OVERLAP=150`
  caracteres iniciales. El encabezado de la sección se antepone al texto del fragmento.
- **Justificación**: las políticas se organizan por secciones cortas; 800 caracteres
  (~150–200 palabras) caben en el límite del modelo de embeddings (512 tokens) con margen,
  y el solapamiento conserva oraciones partidas. Anteponer el encabezado ayuda a la
  recuperación de fragmentos que no repiten el tema ("Se pueden acumular hasta…").
- **Alternativas**: tamaño fijo (corta a mitad de idea); fragmentación semántica por
  embeddings (más lenta y difícil de explicar); por tokens (requiere el tokenizador; los
  caracteres son más fáciles de configurar para el operador).
- **Validación**: se calibra en T-39 con 400/800/1200 y se registra el efecto.

## R-04 — Modelo de embeddings y almacén vectorial

- **Decisión**: `intfloat/multilingual-e5-small` local con `sentence-transformers` en CPU,
  y ChromaDB persistente con distancia coseno.
- **Justificación**: el modelo es multilingüe (español), pequeño (~118 M parámetros,
  384 dimensiones), gratis y evita una segunda API key (sugerencia del enunciado). Chroma
  corre embebido sin servidor, persiste en disco y guarda metadatos (fuente, sección,
  página, versión) con filtros `$in`, necesarios para citar y para el cambio de versión.
- **Detalles que no se pueden olvidar**: e5 exige prefijos `query: ` y `passage: `; la
  colección se crea con `hnsw:space = cosine` (por defecto Chroma usa L2) y la similitud se
  calcula como `1 − distancia`.
- **Riesgo**: `sentence-transformers` trae `torch`, que pesa. Mitigación: instalar torch
  solo CPU (índice de PyTorch para CPU en Linux/Docker) y avisar en el README de la descarga
  inicial del modelo. Alternativa preparada: `fastembed` (ONNX, sin torch) detrás de la
  misma interfaz `Embedder`; si la instalación medida en T-12 resulta inaceptable, se cambia
  y se registra en el spec.
- **Alternativas**: embeddings de OpenAI/Gemini (segunda API key y costo); FAISS (sin
  metadatos ni persistencia integrados); LanceDB y sqlite-vec (válidos, menos documentación
  para filtros por metadatos); Qdrant en Docker (exige Docker al evaluador).

## R-05 — Modelo de lenguaje por defecto

- **Decisión**: Anthropic `claude-haiku-5-5`, con adaptadores para OpenAI (y APIs
  compatibles mediante `base_url`) y Gemini opcional.
- **Criterios**: costo por consulta bajo, latencia baja, buen español, buen seguimiento de
  instrucciones de formato (marcadores `[n]` y `NO_ENCONTRADO`). Los precios y el ID exacto
  se confirman en T-21 con la documentación oficial y el cálculo de costo va en
  `docs/CRITERIO_TECNICO.md`.
- **Alternativas**: modelos grandes (mejor razonamiento, innecesario para extraer
  respuestas de fragmentos; varias veces más caros y lentos); modelos locales con Ollama
  (sin costo, pero exigen hardware al evaluador; cubiertos igual por el adaptador
  compatible con OpenAI).

## R-06 — Prompt de sistema y estrategia contra respuestas inventadas

- **Decisión**: prompt en `prompts/system_prompt.md`; cuatro defensas: (1) rechazo por
  umbral antes del LLM, (2) instrucciones de responder solo con los fragmentos y devolver
  exactamente `NO_ENCONTRADO` si no alcanzan, (3) fragmentos numerados con marcadores `[n]`
  y citas construidas por el código, (4) temperatura baja.
- **Alternativas**: pedir al modelo que escriba las citas (puede inventar nombres de
  archivo); una segunda llamada de verificación (duplica el costo; se deja para producción).

## R-07 — Historial conversacional

- **Decisión**: SQLite con los últimos `HISTORY_LENGTH` mensajes por usuario; las preguntas
  de seguimiento se reformulan con una llamada corta al LLM solo cuando hay historial.
- **Justificación**: sobrevive reinicios (CA-05.4), aísla por `user_id`, sin servidor.
- **Alternativas**: memoria del proceso (se pierde al reiniciar); `persistence` de
  python-telegram-bot (pickle opaco, difícil de consultar y de borrar por usuario).

## R-08 — Configuración y secretos

- **Decisión**: `pydantic-settings`: secretos en `.env` / entorno; el resto en
  `config.yaml` versionado, sobrescribible por variables de entorno con los nombres del
  enunciado; validación al arrancar.
- **Justificación**: el YAML documenta y agrupa (con comentarios) decenas de parámetros;
  `.env` queda mínimo y es lo único que el evaluador edita. La validación tipada da errores
  claros (CA-R1.3).
- **Alternativas**: todo en `.env` (largo, sin estructura, mezcla secretos y ajustes); TOML
  (equivalente; YAML es más conocido por perfiles no Python).

## R-09 — Indexación incremental y concurrencia

- **Decisión**: manifest con huella SHA-256 por archivo; cambio de versión por documento
  (`doc_key`) para actualizaciones incrementales; colección nueva + cambio de puntero para
  reconstrucciones completas; indexación en hilo con un candado no bloqueante.
- **Justificación**: la huella detecta cambios reales de contenido (la fecha de
  modificación cambia al copiar o clonar); el cambio de versión cumple CA-09.2 sin bloquear
  consultas.
- **Alternativas**: comparar fechas o nombres (falsos positivos y negativos); candado
  global que bloquea preguntas durante la reindexación (simple, pero incumple CA-08.4).

## R-10 — Disparadores de reindexación

- **Decisión**: al arrancar + `/reindex` + tarea diaria con JobQueue en el mismo proceso.
- **Alternativas**: *watcher* de carpeta con `watchdog` (archivos a medio copiar, ráfagas
  de eventos); cron/Programador de tareas del sistema (segundo proceso escribiendo el mismo
  índice, pasos extra de instalación).

## R-11 — Formato de las respuestas

- **Decisión**: texto plano, sin `parse_mode`.
- **Justificación**: evita errores de escape de MarkdownV2 con texto del modelo, impide
  enlaces ocultos inyectados y es lo más claro para lectores de pantalla (RF-12).

## R-12 — Herramienta de SDD

- **Decisión**: GitHub Spec Kit 1.3.3 (`specify-cli`) con la integración de Claude Code.
- **Ajuste**: la carpeta de la funcionalidad apunta a `specs/` (en `.specify/feature.json`)
  en lugar de `specs/001-<nombre>/`, para respetar las rutas exigidas por el enunciado.
