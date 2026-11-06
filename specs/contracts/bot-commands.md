# Contrato: interacción en Telegram

> Fase 1 de `/speckit-plan`. Comandos, mensajes y su orden de evaluación. Los textos
> exactos viven en `src/mesa_ayuda/bot/messages.py`.

## Orden de evaluación de cada mensaje

1. Chat no privado → ignorar (CA-07.4).
2. Usuario no autorizado → acceso denegado con su ID (CA-07.2).
3. Límite por minuto superado → aviso (CA-R5.4).
4. Comando → su manejador. Texto → flujo de pregunta. Otro tipo → "solo texto" (CA-R5.3).

## Comandos

| Comando | Quién | Respuesta | Req. |
|---|---|---|---|
| `/start` | autorizado | Bienvenida: qué hace el bot, que responde solo con documentos internos, cómo preguntar | CA-06.1 |
| `/help` | autorizado | Cómo preguntar, ejemplos, lista de comandos; admins ven además `/reindex` | CA-06.2 |
| `/reset` | autorizado | Borra su historial: "Listo, borré nuestra conversación." | CA-06.3 |
| `/fuentes` | autorizado | Lista de documentos `indexed` con número de fragmentos, o "No hay documentos indexados." | CA-06.4 |
| `/reindex` | admin | "Reindexación iniciada…" y luego el resumen | CA-08.1 |
| `/reindex full` | admin | Igual, con reconstrucción completa | CA-08.2 |
| `/reindex` | autorizado no admin | "Este comando es solo para administradores." | CA-08.3 |
| `/reindex` en curso | admin | "Ya hay una reindexación en curso." | CA-08.5 |

## Mensajes del sistema

| Situación | Mensaje |
|---|---|
| Acceso denegado | "No tienes acceso a este asistente. Tu ID de Telegram es {id}; compártelo con el administrador si necesitas acceso." |
| Límite superado | "Has enviado muchos mensajes seguidos. Espera un momento antes de volver a preguntar." |
| No texto / vacío | "Solo puedo responder mensajes de texto. Si prefieres hablar, usa el micrófono del teclado para dictar tu pregunta." |
| No encontrado | "No encontré esa información en los documentos disponibles. Puedes reformular la pregunta o consultar a la mesa de ayuda." |
| LLM no disponible / timeout | Ver [llm-provider.md](llm-provider.md) |
| Error inesperado | "Ocurrió un error inesperado al procesar tu mensaje. Intenta de nuevo." (traza solo en el log) |

## Resumen de reindexación

```text
Reindexación terminada en 4,2 s: 1 nuevo, 1 actualizado, 0 eliminados, 3 sin cambios.
Ilegibles: manual_vpn.pdf (sin texto extraíble).
Advertencias: politica_x.pdf, página 3: texto que parece una instrucción al asistente.
```

## Respuestas largas

Se dividen en partes de máximo 4096 caracteres, cortando en saltos de párrafo o de línea
cuando sea posible; la línea de fuentes va en la última parte.
