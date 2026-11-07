# Contrato: interacción en Telegram

> Fase 1 de `/speckit-plan`. Comandos, mensajes y su orden de evaluación. Los textos
> exactos viven en `src/mesa_ayuda/bot/messages.py`.

## Orden de evaluación de cada mensaje

1. Chat no privado → ignorar (CA-07.4).
2. Usuario no autorizado → acceso denegado con su ID (CA-07.2), salvo `/start <código>` con el `INVITE_CODE` vigente, que lo autoriza y le da la bienvenida (CA-07.5).
3. Mismo texto del mismo usuario dentro de `DUPLICATE_WINDOW_SECONDS` → se descarta sin responder (CA-R5.7).
4. Límite por minuto superado → aviso (CA-R5.4).
5. Comando → su manejador. Texto → flujo de pregunta. Otro tipo → "solo texto" (CA-R5.3).

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
| `/invitacion` | admin | El enlace de invitación, o cómo activarlo si no hay `INVITE_CODE` | CA-07.5 |
| `/invitados` | admin | Invitados con el código vigente: ID, nombre, fecha y preguntas de hoy | CA-07.5 |
| `/revocar <id>` | admin | Retira la invitación de ese usuario | CA-07.5 |

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

## Comportamiento del bot (S6)

Decisiones de implementación que el resto del contrato no fija:

- **Un filtro común antes de todo** (grupo -1 de python-telegram-bot): chat privado,
  acceso y límite por minuto se evalúan una sola vez, en el orden de arriba, para comandos,
  texto y cualquier otro tipo de mensaje. Si un paso rechaza, ningún manejador corre.
- **Aviso de límite una vez por ventana**: el primer mensaje que supera
  `RATE_LIMIT_PER_MINUTE` recibe el aviso; los siguientes de esa misma ventana de 60 s se
  descartan en silencio, para que el bot no conteste a cada mensaje de una ráfaga. Solo
  cuentan los mensajes aceptados.
- **Varios usuarios a la vez, un mensaje por usuario a la vez**: las actualizaciones se
  procesan en paralelo (una pregunta lenta no frena a los demás ni a `/reindex`, CA-08.4),
  pero los mensajes de un mismo usuario se atienden en orden, uno tras otro, para que el
  historial no se mezcle.
- **"Escribiendo…" mientras se responde**: se envía la acción `typing` y se renueva cada
  4 s hasta que sale la respuesta (Telegram la apaga a los 5 s).
- **Historial**: se guardan la pregunta original y la respuesta sin la línea de fuentes
  solo cuando hubo respuesta. Las preguntas no encontradas van a `unanswered` (con la
  reformulada si difiere de la original) y no al historial, para no reformular los
  seguimientos sobre una respuesta vacía. Con `HISTORY_LENGTH: 0` no se guarda nada.
- **`/reindex`** corre en segundo plano: responde "Reindexación iniciada…" de inmediato y
  el resumen llega al terminar. `/reindex algo` (argumento desconocido) responde el uso.
  Las advertencias del resumen salen de los documentos nuevos o actualizados.
- **Comando desconocido**: "No conozco ese comando. Usa /help para ver los disponibles."
- **Mensajes editados**: se ignoran; solo se responden mensajes nuevos.
- **Invitaciones** (CA-07.5): el código se compara en tiempo constante y nunca se escribe en el log; se guarda una huella (SHA-256) del código con que entró cada invitado, así cambiar `INVITE_CODE` los deja a todos fuera sin borrar nada. Un invitado es un usuario normal: no administra.
- **Topes** (RNF-08): el cupo se descuenta antes de llamar al modelo, dentro de una transacción, para que dos mensajes simultáneos no pasen ambos el último cupo. Una pregunta que termina en error del modelo también cuenta. El orden dentro del flujo de pregunta es: texto vacío → saludo → largo máximo → tope diario → servicio de respuesta.
- **Saludos, agradecimientos y despedidas** (CA-06.5): se reconocen solo si el mensaje completo es una de esas fórmulas, sin tildes, signos ni letras repetidas ("Holaaa!!", "buenas tardes", "muchas gracias", "chao"). Si trae algo más, es una pregunta. Se responden con un texto fijo, sin modelo ni historial, y se registran como `outcome=small_talk`.
- **`/start`**: Telegram lo envía solo cuando alguien abre el bot por primera vez (botón "Iniciar"); después no hace falta usarlo, pero sigue disponible.
- **Copias del mismo mensaje**: el texto se compara sin espacios sobrantes ni diferencias de mayúsculas, por usuario, contra el último mensaje aceptado de ese usuario. La ventana cuenta desde la primera copia, así que una ráfaga larga no la alarga sin fin. Las copias descartadas no cuentan para el límite por minuto; se registran como `outcome=duplicate`. Aplica también a los comandos (diez `/start` seguidos reciben una bienvenida). Los mensajes sin texto (fotos, audios) no se comparan.
- **Arranque**: la indexación inicial termina antes de empezar a recibir mensajes. Los
  mensajes enviados mientras el bot estaba apagado se responden al arrancar.

| Situación | Mensaje |
|---|---|
| `/reindex` con argumento desconocido | "Uso: /reindex (cambios) o /reindex full (reconstrucción completa)." |
| La reindexación falla | "La reindexación falló: {motivo}. Revisa el log del bot." |
| Comando desconocido | "No conozco ese comando. Usa /help para ver los disponibles." |
| Invitación aceptada | La bienvenida de `/start` |
| Tope diario del usuario (CA-R8.1) | "Llegaste al máximo de {n} preguntas por hoy. Vuelve a intentarlo mañana." |
| Tope diario total (CA-R8.2) | "El asistente alcanzó el máximo de preguntas de hoy. Vuelve a intentarlo mañana." |
| Pregunta muy larga (CA-R8.3) | "Tu pregunta es muy larga (máximo {n} caracteres). Resúmela en una o dos frases." |
| Saludo (CA-06.5) | "Hola, {nombre}. Con gusto te ayudo con las políticas, procedimientos y guías de la empresa. Escríbeme tu pregunta, por ejemplo: ¿Cuántos días de vacaciones tengo? Usa /help para ver cómo preguntar y los comandos." |
| Agradecimiento | "Con gusto. Si tienes otra pregunta, aquí estoy." |
| Despedida | "Hasta pronto. Cuando necesites algo, escríbeme." |
