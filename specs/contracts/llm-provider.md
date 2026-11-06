# Contrato: proveedor de LLM y embeddings

> Fase 1 de `/speckit-plan`. Interfaces internas que permiten cambiar de proveedor solo con
> configuración (CA-R1.4) y probar sin red (principio V).

## `LLMProvider`

```python
@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str

@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str

class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult: ...
```

**Errores** (toda excepción del SDK se traduce a una de estas):

| Excepción | Cuándo | Mensaje al usuario (`bot/messages.py`) |
|---|---|---|
| `LLMTimeoutError` | Se superó `LLM_TIMEOUT_SECONDS` | "La respuesta está tardando más de lo normal. Intenta de nuevo en un momento." |
| `LLMUnavailableError` | 5xx, 429 o red, después de `LLM_MAX_RETRIES` reintentos con espera exponencial | "El servicio de respuestas no está disponible en este momento. Intenta de nuevo en unos minutos." |
| `LLMAuthError` | 401/403 (API key inválida) | Igual que no disponible; log `ERROR` claro para el operador |
| `LLMBadRequestError` | 400 (modelo inexistente, parámetros) | Igual que no disponible; log `ERROR` con el detalle |

**Implementaciones**: `AnthropicProvider`, `OpenAIProvider` (acepta `base_url` para APIs
compatibles), `GeminiProvider` (opcional), `FakeProvider` (pruebas: respuestas programadas
y registro de llamadas). Fábrica: `create_provider(settings) -> LLMProvider`.

## `Embedder`

```python
class Embedder(Protocol):
    model_id: str          # se guarda en el manifest; si cambia → reconstrucción
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
```

- `LocalEmbedder` (fastembed, ONNX en CPU) carga el modelo de `EMBEDDING_MODEL` la primera
  vez que se usa, antepone los prefijos que exige la familia del modelo (e5: `passage: ` /
  `query: `) y devuelve vectores normalizados. Acepta los modelos del catálogo de fastembed y
  los registrados en el proyecto (e5-small); otro nombre falla al arrancar con un mensaje
  que lista las opciones (`EmbeddingModelError`).
- `FakeEmbedder` (pruebas): vectores deterministas a partir de bolsa de palabras, de modo
  que textos que comparten palabras quedan cerca.
